import { useRef, useEffect, useCallback } from 'react'
import useEmblaCarousel from 'embla-carousel-react'
import { getImageUrl } from '../api'

// Debug logging - enabled in development, disabled in production
const DEBUG = import.meta.env.DEV
const log = (...args) => DEBUG && console.log('🎠 [carousel]', ...args)

const Carousel = ({ 
  displayItems, 
  selectedIndex, 
  setSelectedIndex,
  nowPlaying,
  deleteMode,
  setDeleteMode,
  deleting,
  saving,
  onPlay,
  onSaveContext,
  onDeleteItem,
  onTogglePlayPause,
  isItemPlaying,
  scrollToIndex,
  setScrollToIndex
}) => {
  const [emblaRef, emblaApi] = useEmblaCarousel({
    align: 'center',
    containScroll: false,
    skipSnaps: true,
  })

  // Refs for stable callbacks (avoid re-renders from prop changes)
  const displayItemsRef = useRef(displayItems)
  const onPlayRef = useRef(onPlay)
  const isItemPlayingRef = useRef(isItemPlaying)
  const longPressTimerRef = useRef(null)
  const playTimerRef = useRef(null)
  const lastPlayedByUsRef = useRef(null)
  const userInteractingRef = useRef(false)
  const emblaInitializedRef = useRef(false)
  
  // Keep refs up to date
  useEffect(() => {
    displayItemsRef.current = displayItems
  }, [displayItems])
  
  useEffect(() => {
    onPlayRef.current = onPlay
  }, [onPlay])
  
  useEffect(() => {
    isItemPlayingRef.current = isItemPlaying
  }, [isItemPlaying])

  // Cleanup timers on unmount
  useEffect(() => () => {
    clearTimeout(playTimerRef.current)
    clearTimeout(longPressTimerRef.current)
  }, [])

  // Start play timer for an item
  const startPlayTimer = useCallback((item) => {
    // Cancel existing timer
    if (playTimerRef.current) {
      log('⏱️ Timer cancelled (new selection)')
      clearTimeout(playTimerRef.current)
      playTimerRef.current = null
    }

    if (!item) return

    // Don't start timer if already playing
    if (isItemPlayingRef.current(item)) {
      log('⏱️ Timer skipped (already playing):', item.name)
      return
    }

    log('⏱️ Timer starting for:', item.name)
    
    playTimerRef.current = setTimeout(async () => {
      playTimerRef.current = null
      
      // Double check it's still not playing
      if (isItemPlayingRef.current(item)) {
        log('⏱️ Timer fired but item now playing, skipping')
        return
      }
      
      log('⏱️ Timer fired! Playing:', item.name)
      lastPlayedByUsRef.current = item.uri
      await onPlayRef.current(item)
    }, 1000)
  }, [])

  // Cancel play timer
  const cancelPlayTimer = useCallback(() => {
    if (playTimerRef.current) {
      log('⏱️ Timer cancelled')
      clearTimeout(playTimerRef.current)
      playTimerRef.current = null
    }
  }, [])

  // Subscribe to carousel events (only once when emblaApi is ready)
  useEffect(() => {
    if (!emblaApi) return
    
    // Only log on first init
    if (!emblaInitializedRef.current) {
      log('Embla initialized, items:', displayItemsRef.current.length)
      emblaInitializedRef.current = true
    }
    
    const handleSelect = () => {
      const newIndex = emblaApi.selectedScrollSnap()
      log('onSelect:', newIndex)
      setSelectedIndex(newIndex)
    }
    
    const handleSettle = () => {
      const currentIndex = emblaApi.selectedScrollSnap()
      const currentItem = displayItemsRef.current[currentIndex]
      log('onSettle: index', currentIndex, 'item:', currentItem?.name)
      startPlayTimer(currentItem)
      userInteractingRef.current = false
    }
    
    const handlePointerDown = () => {
      userInteractingRef.current = true
      cancelPlayTimer()
    }
    
    emblaApi.on('select', handleSelect)
    emblaApi.on('settle', handleSettle)
    emblaApi.on('pointerDown', handlePointerDown)
    handleSelect() // Initial call
    
    return () => {
      emblaApi.off('select', handleSelect)
      emblaApi.off('settle', handleSettle)
      emblaApi.off('pointerDown', handlePointerDown)
      cancelPlayTimer()
    }
  }, [emblaApi, setSelectedIndex, startPlayTimer, cancelPlayTimer])

  // Handle click/tap on a specific slide
  const handleSlideClick = useCallback((clickedIndex) => {
    if (!emblaApi) return
    
    const currentSnap = emblaApi.selectedScrollSnap()
    const clickedItem = displayItemsRef.current[clickedIndex]
    
    log('Slide clicked:', clickedIndex, 'current:', currentSnap, 'item:', clickedItem?.name)
    
    if (clickedIndex === currentSnap) {
      // Clicked on active slide - toggle play/pause
      log('Toggle play/pause on active slide')
      onTogglePlayPause()
    } else {
      // Clicked on different slide - scroll to it (will trigger settle -> auto-play)
      log('Scrolling to clicked slide:', clickedIndex)
      userInteractingRef.current = true
      cancelPlayTimer()
      emblaApi.scrollTo(clickedIndex)
    }
  }, [emblaApi, onTogglePlayPause, cancelPlayTimer])

  // Handle external scroll requests (e.g., scroll back after failure)
  useEffect(() => {
    if (!emblaApi || scrollToIndex === null) return
    
    log('External scroll request to index:', scrollToIndex)
    cancelPlayTimer() // Cancel any pending timer
    emblaApi.scrollTo(scrollToIndex, true) // instant scroll
    setSelectedIndex(scrollToIndex)
    setScrollToIndex(null)
  }, [emblaApi, scrollToIndex, setScrollToIndex, setSelectedIndex, cancelPlayTimer])

  // Sync carousel to playing context (on mount and when context changes)
  const nowPlayingContextUri = nowPlaying?.context?.uri
  const nowPlayingTrackUri = nowPlaying?.track?.uri
  const nowPlayingAlbum = nowPlaying?.track?.album
  
  useEffect(() => {
    if (!emblaApi || displayItemsRef.current.length === 0) return
    if (userInteractingRef.current) return
    if (playTimerRef.current) return // Don't sync while timer is active
    
    const contextUri = nowPlayingContextUri
    const trackUri = nowPlayingTrackUri
    
    // Skip sync for item we just started playing
    if (contextUri === lastPlayedByUsRef.current) {
      log('Skipping sync - we just played this')
      lastPlayedByUsRef.current = null
      return
    }
    
    if (!contextUri && !trackUri) return
    
    // Find playing item in displayItems
    const items = displayItemsRef.current
    const playingIndex = items.findIndex(item => {
      if (item.uri === contextUri) return true
      if (item.currentTrack?.uri === trackUri) return true
      // Match by album name as fallback
      if (nowPlayingAlbum && (item.name === nowPlayingAlbum || item.album === nowPlayingAlbum)) return true
      return false
    })
    
    if (playingIndex === -1) return
    
    const currentSnap = emblaApi.selectedScrollSnap()
    if (currentSnap === playingIndex) return
    
    log('Syncing to playing item:', playingIndex, items[playingIndex]?.name)
    emblaApi.scrollTo(playingIndex, true) // instant scroll
    setSelectedIndex(playingIndex)
  }, [emblaApi, nowPlayingContextUri, nowPlayingTrackUri, nowPlayingAlbum, setSelectedIndex])

  // Reinitialize when displayItems count changes
  const itemsCount = displayItems.length
  useEffect(() => {
    if (!emblaApi || itemsCount === 0) return
    
    log('ReInit - items count:', itemsCount)
    emblaApi.reInit()
    
    // Find and scroll to playing item
    const contextUri = nowPlayingContextUri
    if (contextUri) {
      const items = displayItemsRef.current
      const playingIndex = items.findIndex(item => item.uri === contextUri)
      if (playingIndex !== -1) {
        log('ReInit scrolling to playing:', playingIndex)
        emblaApi.scrollTo(playingIndex, true)
        setSelectedIndex(playingIndex)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [emblaApi, itemsCount])

  // Long press for delete mode
  const handlePressStart = (item) => {
    if (item.isTemp) return
    longPressTimerRef.current = setTimeout(() => {
      setDeleteMode(item.id)
    }, 1000)
  }

  const handlePressEnd = () => {
    clearTimeout(longPressTimerRef.current)
  }

  // Render album cover
  const renderCover = (item) => {
    if (item.uri?.includes('playlist') || item.type === 'playlist') {
      const covers = item.images || []
      return (
        <div className="composite-cover">
          {[0, 1, 2, 3].map((i) => {
            const img = covers[i]
            return img 
              ? <img key={i} src={getImageUrl(img)} alt="" />
              : <div key={i} className="composite-empty" />
          })}
        </div>
      )
    }
    
    if (!item.image) {
      return <div className="slide-img slide-placeholder" />
    }
    return <img src={getImageUrl(item.image)} alt={item.name} className="slide-img" />
  }

  const isPaused = nowPlaying?.paused
  const isBuffering = nowPlaying?.buffering
  const track = nowPlaying?.track

  return (
    <div className="carousel-container">
      <div className="embla" ref={emblaRef}>
        <div className="embla__container">
          {displayItems.map((item, index) => {
            const isPlayingPaused = isItemPlaying(item) && isPaused
            const isInDeleteMode = deleteMode === item.id
            return (
              <div 
                key={item.id} 
                className={`embla__slide ${index === selectedIndex ? 'is-selected' : ''} ${item.isTemp ? 'is-temp' : ''} ${isPlayingPaused ? 'is-playing-paused' : ''} ${isInDeleteMode ? 'is-delete-mode' : ''}`}
                onPointerDown={() => handlePressStart(item)}
                onPointerUp={handlePressEnd}
                onPointerLeave={handlePressEnd}
                onPointerCancel={handlePressEnd}
                onClick={() => handleSlideClick(index)}
              >
                <div className="slide-content">
                  {renderCover(item)}
                  {isItemPlaying(item) && isBuffering && !track && (
                    <div className="buffering-overlay">
                      <div className="buffering-spinner" />
                    </div>
                  )}
                  {/* Save button for temp items */}
                  {item.isTemp && (
                    <button 
                      className="cover-save-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        onSaveContext()
                      }}
                      disabled={saving}
                      aria-label="Save to catalog"
                    >
                      {saving ? (
                        <div className="save-spinner" />
                      ) : (
                        <svg viewBox="0 0 24 24" fill="currentColor">
                          <rect x="5" y="10.5" width="14" height="3" rx="1.5"/>
                          <rect x="10.5" y="5" width="3" height="14" rx="1.5"/>
                        </svg>
                      )}
                    </button>
                  )}
                  {/* Delete button */}
                  {isInDeleteMode && (
                    <button 
                      className="cover-delete-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        onDeleteItem(item.id)
                      }}
                      disabled={deleting}
                      aria-label="Remove from catalog"
                    >
                      {deleting ? (
                        <div className="save-spinner" />
                      ) : (
                        <svg viewBox="0 0 24 24" fill="currentColor">
                          <rect x="5" y="10.5" width="14" height="3" rx="1.5"/>
                        </svg>
                      )}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default Carousel
