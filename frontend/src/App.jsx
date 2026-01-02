import { useState, useEffect, useCallback, useRef } from 'react'
import { api, createWebSocket } from './api'
import Carousel from './components/Carousel'
import { PlaybackControls, ProgressBar, useProgressAnimation } from './components/Controls'
import Toast from './components/Toast'
import SleepOverlay from './components/SleepOverlay'
import { useSleepMode } from './hooks/useSleepMode'
import { useTempItem } from './hooks/useTempItem'
import './App.css'

// Debug logging - enabled in development, disabled in production
const DEBUG = import.meta.env.DEV
const log = (...args) => DEBUG && console.log('📱 [app]', ...args)

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
  const [scrollToIndex, setScrollToIndex] = useState(null) // For scroll-back after failure
  
  const suppressUntilPlayRef = useRef(false)
  const toastTimeoutRef = useRef(null)
  const pendingPlayRef = useRef(null) // Prevent duplicate play requests
  
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

  // Refresh catalog from server
  const refreshCatalog = useCallback(async () => {
    const data = await api.getCatalog()
    const filteredItems = (data.items || []).filter(item => item.type !== 'track')
    setCatalog({ ...data, items: filteredItems })
  }, [])

  // Fetch catalog periodically
  useEffect(() => {
    const fetchCatalog = async () => {
      try {
        const data = await api.getCatalog()
        const filteredItems = (data.items || []).filter(item => item.type !== 'track')
        setCatalog({ ...data, items: filteredItems })
        log('Catalog loaded:', filteredItems.length, 'items')
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
        
        log('🔌 [ws] Message:', 
          'context:', data.context?.uri?.slice(-20), 
          'track:', data.track?.name,
          'playing:', data.playing
        )
        
        if (data.cleared) {
          suppressUntilPlayRef.current = true
          setNowPlaying(null)
          return
        }
        
        if (suppressUntilPlayRef.current) return
        
        setNowPlaying(data)
      } catch (err) {
        console.error('Error parsing WebSocket message:', err)
      }
    }

    const handleReconnect = () => {
      log('🔌 [ws] Reconnected')
    }

    const cleanup = createWebSocket(handleMessage, handleReconnect)
    return cleanup
  }, [])

  // Check if an item is currently playing
  const isItemPlaying = useCallback((item) => {
    return item?.uri === nowPlaying?.context?.uri
  }, [nowPlaying?.context?.uri])

  // Play an item - called by Carousel after 1s timer
  const handlePlay = useCallback(async (item) => {
    log('▶️ [play] Request:', item?.name, item?.uri?.slice(-20))
    
    if (!item) {
      return { success: true }
    }
    
    // Skip if already playing OR if we have a pending request for this item
    if (isItemPlaying(item) || pendingPlayRef.current === item.uri) {
      log('▶️ [play] Skipped (already playing or pending)')
      return { success: true }
    }
    
    pendingPlayRef.current = item.uri
    suppressUntilPlayRef.current = false
    
    try {
      const result = await api.play(item.uri)
      log('▶️ [play] Result:', result.success ? '✅' : '❌', result.reason || '')
      
      if (!result.success) {
        // Show error toast
        if (result.reason === 'unavailable') {
          showToast(TOAST.UNAVAILABLE)
        } else {
          showToast(TOAST.PLAY_FAILED)
        }
        
        // Scroll back to the playing item
        const playingIdx = findPlayingIndex(displayItems, nowPlaying?.context?.uri, nowPlaying?.track?.uri)
        if (playingIdx !== -1) {
          log('↩️ [play] Scrolling back to playing item:', playingIdx)
          setScrollToIndex(playingIdx)
        }
        
        return result
      }
      
      return result
    } catch (err) {
      console.error('Error playing:', err)
      log('▶️ [play] Exception:', err.message)
      showToast(TOAST.PLAY_FAILED)
      return { success: false, reason: 'error' }
    } finally {
      pendingPlayRef.current = null
    }
  }, [isItemPlaying, showToast, findPlayingIndex, displayItems, nowPlaying?.context?.uri, nowPlaying?.track?.uri])

  // Player controls
  const next = () => api.next()
  
  const prev = () => {
    const position = nowPlaying?.track?.position || 0
    return position < 10000 ? api.prev() : api.seek(0)
  }
  
  const togglePlayPause = useCallback(() => {
    if (nowPlaying?.playing) {
      return api.pause()
    } else if (nowPlaying?.paused) {
      suppressUntilPlayRef.current = false
      return api.resume()
    } else {
      // Nothing playing, play the selected item
      const selectedItem = displayItems[selectedIndex]
      if (selectedItem) {
        return handlePlay(selectedItem)
      }
    }
  }, [nowPlaying?.playing, nowPlaying?.paused, displayItems, selectedIndex, handlePlay])

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
        await refreshCatalog()
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
        await refreshCatalog()
        setDeleteMode(null)
        
        const newLength = catalog.items.length - 1
        if (newLength > 0) {
          const newIndex = deleteIndex >= newLength ? newLength - 1 : deleteIndex
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
  
  // Progress bar animation
  const { progress, shouldAnimate, currentTime, totalTime } = useProgressAnimation(track, isPlaying)
  
  const contextUri = nowPlaying?.context?.uri || ''
  const trackUri = nowPlaying?.track?.uri || ''
  const hasContext = contextUri.includes('playlist') || contextUri.includes('album')
  const isContextSaved = catalog.items.some(item => 
    item.uri === contextUri || item.currentTrack?.uri === trackUri
  )

  // Determine what track info to display
  // Priority: 1) Now playing track, 2) Saved track from selected item, 3) Nothing
  const selectedItem = displayItems[selectedIndex]
  const displayTrack = (() => {
    // If the selected item is playing, show the actual playing track
    if (isItemPlaying(selectedItem) && track) {
      return { name: track.name, artist: track.artist }
    }
    
    // If selected item has saved progress, show that track info
    if (selectedItem?.currentTrack?.name) {
      return { 
        name: selectedItem.currentTrack.name, 
        artist: selectedItem.currentTrack.artist 
      }
    }
    
    // No track info available - show loading or empty
    return null
  })()

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
            onPlay={handlePlay}
            onSaveContext={saveContext}
            onDeleteItem={deleteFromCatalog}
            onTogglePlayPause={togglePlayPause}
            isItemPlaying={isItemPlaying}
            scrollToIndex={scrollToIndex}
            setScrollToIndex={setScrollToIndex}
          />

          {/* Track Info - show saved track or now playing */}
          <div className="track-info">
            {displayTrack ? (
              <>
                <h2 className="track-name">{displayTrack.name}</h2>
                <p className="artist-name">{displayTrack.artist}</p>
              </>
            ) : nowPlaying?.playing ? (
              <>
                <h2 className="track-name track-loading">Loading...</h2>
                <p className="artist-name">&nbsp;</p>
              </>
            ) : (
              <>
                <h2 className="track-name track-loading">Tap play to start</h2>
                <p className="artist-name">&nbsp;</p>
              </>
            )}
          </div>

          <ProgressBar 
            progress={isItemPlaying(selectedItem) ? progress : 0}
            shouldAnimate={isItemPlaying(selectedItem) ? shouldAnimate : false}
            currentTime={isItemPlaying(selectedItem) ? currentTime : '0:00'}
            totalTime={isItemPlaying(selectedItem) ? totalTime : '0:00'}
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
