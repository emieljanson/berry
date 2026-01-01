import { useState, useEffect, useCallback, useRef } from 'react'
import { api, createWebSocket } from './api'
import Carousel from './components/Carousel'
import { PlaybackControls, ProgressBar, useProgressAnimation } from './components/Controls'
import Toast from './components/Toast'
import SleepOverlay from './components/SleepOverlay'
import { useSleepMode } from './hooks/useSleepMode'
import { useTempItem } from './hooks/useTempItem'
import './App.css'

// Toast messages
const TOAST = {
  PLAY_FAILED: 'Cannot play',
  UNAVAILABLE: 'Not available on this account',
  OFFLINE: 'No connection',
  SAVE_FAILED: 'Save failed',
  DELETE_FAILED: 'Delete failed'
}

function App() {
  const [catalog, setCatalog] = useState({ items: [] })
  const [nowPlaying, setNowPlaying] = useState(null)
  const [saving, setSaving] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [deleteMode, setDeleteMode] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const [toast, setToast] = useState(null)
  const [pendingItem, setPendingItem] = useState(null)
  
  const suppressUntilPlayRef = useRef(false)
  const toastTimeoutRef = useRef(null)
  
  // Sleep mode - screen off after inactivity when not playing
  const isScreenOff = useSleepMode(nowPlaying?.playing)
  
  // Temp item for music not in catalog
  const { tempItem, displayItems, findPlayingIndex } = useTempItem(catalog.items, nowPlaying)

  // Show toast notification
  const showToast = useCallback((message) => {
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current)
    setToast({ message, visible: true })
    toastTimeoutRef.current = setTimeout(() => {
      setToast(prev => prev ? { ...prev, visible: false } : null)
    }, 4000)
  }, [])

  // Fetch catalog periodically
  useEffect(() => {
    const fetchCatalog = async () => {
      try {
        const data = await api.getCatalog()
        const filteredItems = (data.items || []).filter(item => item.type !== 'track')
        setCatalog({ ...data, items: filteredItems })
      } catch (err) {
        console.error('Error fetching catalog:', err)
      }
    }
    
    fetchCatalog()
    const interval = setInterval(fetchCatalog, 10000)
    return () => clearInterval(interval)
  }, [])

  // WebSocket connection for real-time state updates
  useEffect(() => {
    const handleMessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        
        if (data.cleared) {
          suppressUntilPlayRef.current = true
          setNowPlaying(null)
          setPendingItem(null)
          return
        }
        
        if (suppressUntilPlayRef.current) return
        
        setNowPlaying(data)
      } catch (err) {
        console.error('Error parsing WebSocket message:', err)
      }
    }

    const handleReconnect = () => {
      // Clear pending item on reconnect to force carousel sync
      setPendingItem(null)
    }

    const cleanup = createWebSocket(handleMessage, handleReconnect)
    return cleanup
  }, [])

  // Check if an item is currently playing
  const isItemPlaying = useCallback((item) => {
    return item?.uri === nowPlaying?.context?.uri
  }, [nowPlaying?.context?.uri])

  // Play an item if not already playing
  const selectAndPlay = useCallback(async (item) => {
    if (!item || isItemPlaying(item)) return
    
    suppressUntilPlayRef.current = false
    
    try {
      const result = await api.play(item.uri)
      if (!result.success) {
        if (result.reason === 'unavailable') {
          showToast(TOAST.UNAVAILABLE)
        } else {
          showToast(TOAST.PLAY_FAILED)
        }
      }
    } catch (err) {
      console.error('Error playing:', err)
      showToast(TOAST.PLAY_FAILED)
    }
  }, [isItemPlaying, showToast])

  // Handle slide click from carousel
  const onSlideClick = useCallback((item, index) => {
    if (item && isItemPlaying(item)) {
      if (nowPlaying?.playing) {
        api.pause()
      } else {
        suppressUntilPlayRef.current = false
        api.resume()
      }
    } else {
      selectAndPlay(item)
    }
  }, [selectAndPlay, isItemPlaying, nowPlaying?.playing])

  // Player controls
  const pause = () => api.pause()
  const resume = () => api.resume()
  const next = () => api.next()
  
  const prev = () => {
    const position = nowPlaying?.track?.position || 0
    return position < 10000 ? api.prev() : api.seek(0)
  }
  
  const togglePlayPause = useCallback(() => {
    if (pendingItem) {
      // There's a pending item from swipe - play it directly (no delay)
      selectAndPlay(pendingItem)
      setPendingItem(null)
      return
    }
    
    if (nowPlaying?.playing) {
      return pause()
    } else if (nowPlaying?.paused) {
      suppressUntilPlayRef.current = false
      return resume()
    } else {
      const selectedItem = displayItems[selectedIndex]
      if (selectedItem) {
        return selectAndPlay(selectedItem)
      }
    }
  }, [pendingItem, nowPlaying?.playing, nowPlaying?.paused, displayItems, selectedIndex, selectAndPlay])

  // Save context to catalog
  const saveContext = async () => {
    const contextUri = nowPlaying?.context?.uri
    if (!contextUri || !nowPlaying?.track) return
    
    setSaving(true)
    
    try {
      const isPlaylist = contextUri.includes('playlist')
      const isAlbum = contextUri.includes('album')
      
      const data = await api.saveToCatalog({
        type: isPlaylist ? 'playlist' : 'album',
        uri: contextUri,
        name: isAlbum ? nowPlaying.track.album : 'Playlist',
        artist: nowPlaying.track.artist,
        album: nowPlaying.track.album,
        image: nowPlaying.track.albumCover
      })
      
      if (data.success) {
        const catalogData = await api.getCatalog()
        const filteredItems = (catalogData.items || []).filter(item => item.type !== 'track')
        setCatalog({ ...catalogData, items: filteredItems })
      }
    } catch (err) {
      console.error('Error saving:', err)
      showToast(TOAST.SAVE_FAILED)
    }
    
    setSaving(false)
  }

  // Delete item from catalog
  const deleteFromCatalog = async (itemId) => {
    setDeleting(true)
    try {
      const deleteIndex = displayItems.findIndex(item => item.id === itemId)
      
      const result = await api.deleteFromCatalog(itemId)
      if (result.success) {
        const data = await api.getCatalog()
        const filteredItems = (data.items || []).filter(item => item.type !== 'track')
        setCatalog({ ...data, items: filteredItems })
        setDeleteMode(null)
        
        if (filteredItems.length > 0) {
          const newIndex = deleteIndex >= filteredItems.length 
            ? filteredItems.length - 1 
            : deleteIndex
          setSelectedIndex(newIndex)
        }
      }
    } catch (err) {
      console.error('Error deleting:', err)
      showToast(TOAST.DELETE_FAILED)
    }
    setDeleting(false)
  }

  // Cancel delete mode when clicking elsewhere
  const handleAppClick = (e) => {
    if (deleteMode && !e.target.closest('.cover-delete-btn') && !e.target.closest('.embla__slide')) {
      setDeleteMode(null)
    }
  }

  // Derived state
  const track = nowPlaying?.track
  const isPlaying = nowPlaying?.playing && !!track
  const isPaused = nowPlaying?.paused
  
  // Progress bar animation
  const { progress, shouldAnimate, currentTime, totalTime } = useProgressAnimation(track, isPlaying)
  
  const contextUri = nowPlaying?.context?.uri || ''
  const trackUri = nowPlaying?.track?.uri || ''
  const hasContext = contextUri.includes('playlist') || contextUri.includes('album')
  const isContextSaved = catalog.items.some(item => 
    item.uri === contextUri || item.currentTrack?.uri === trackUri
  )

  return (
    <div className="app" onClick={handleAppClick}>
      <SleepOverlay isActive={isScreenOff} />
      <Toast toast={toast} />
      
      {displayItems.length > 0 ? (
        <>
          <Carousel
            displayItems={displayItems}
            selectedIndex={selectedIndex}
            setSelectedIndex={setSelectedIndex}
            nowPlaying={nowPlaying}
            deleteMode={deleteMode}
            setDeleteMode={setDeleteMode}
            deleting={deleting}
            saving={saving}
            onSlideClick={onSlideClick}
            onSaveContext={saveContext}
            onDeleteItem={deleteFromCatalog}
            onTogglePlayPause={togglePlayPause}
            isItemPlaying={isItemPlaying}
            findPlayingIndex={findPlayingIndex}
            pendingItem={pendingItem}
            setPendingItem={setPendingItem}
          />

          {/* Track Info - show pending item info if swiping, otherwise nowPlaying */}
          <div className="track-info">
            {pendingItem ? (
              <>
                <h2 className="track-name">{pendingItem.name || 'Loading...'}</h2>
                <p className="artist-name">{pendingItem.artist || ''}</p>
              </>
            ) : track ? (
              <>
                <h2 className="track-name">{track.name}</h2>
                <p className="artist-name">{track.artist}</p>
              </>
            ) : (
              <>
                <h2 className="track-name">{displayItems[selectedIndex]?.name || 'Select an album'}</h2>
                <p className="artist-name">{displayItems[selectedIndex]?.artist || ''}</p>
              </>
            )}
          </div>

          <ProgressBar 
            progress={pendingItem ? 0 : progress}
            shouldAnimate={pendingItem ? false : shouldAnimate}
            currentTime={pendingItem ? '0:00' : currentTime}
            totalTime={pendingItem ? '0:00' : totalTime}
          />

          <PlaybackControls 
            isPlaying={isPlaying}
            onPrev={prev}
            onToggle={togglePlayPause}
            onNext={next}
          />
        </>
      ) : (
        /* Empty State */
        <div className="empty-state">
          <div className="empty-icon">🎧</div>
          <h2>No music yet</h2>
          <p>Play music via Spotify and tap + to add</p>
          
          {track && (
            <div className="current-track-preview">
              <img src={track.albumCover} alt="" className="preview-cover" />
              <div className="preview-info">
                <p className="preview-name">{track.name}</p>
                <p className="preview-artist">{track.artist}</p>
              </div>
              
              <PlaybackControls 
                isPlaying={isPlaying}
                onPrev={prev}
                onToggle={togglePlayPause}
                onNext={next}
                compact
              />
              
              {hasContext && !isContextSaved && (
                <button 
                  className="save-btn-inline"
                  onClick={saveContext}
                  disabled={saving}
                >
                  {saving ? 'Saving...' : '+ Add to Berry'}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default App
