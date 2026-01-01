import { useRef, useEffect, useCallback } from 'react'
import useEmblaCarousel from 'embla-carousel-react'
import { getImageUrl } from '../api'

const Carousel = ({ 
  displayItems, 
  selectedIndex, 
  setSelectedIndex,
  nowPlaying,
  deleteMode,
  setDeleteMode,
  deleting,
  saving,
  onSlideClick,
  onSaveContext,
  onDeleteItem,
  onTogglePlayPause,
  isItemPlaying,
  findPlayingIndex,
  pendingItem,
  setPendingItem
}) => {
  const [emblaRef, emblaApi] = useEmblaCarousel({
    align: 'center',
    containScroll: false,
    skipSnaps: true,
  })

  // Core refs
  const displayItemsRef = useRef(displayItems)
  const longPressTimerRef = useRef(null)
  const playTimerRef = useRef(null)
  const lastPlayedByUsRef = useRef(null)
  const lastSyncedContextRef = useRef(null)
  const isSyncingRef = useRef(false)
  const userInteractingRef = useRef(false)
  const pointerDownSnapRef = useRef(null)
  
  useEffect(() => {
    displayItemsRef.current = displayItems
  }, [displayItems])

  // Cleanup timers on unmount
  useEffect(() => () => {
    clearTimeout(playTimerRef.current)
    clearTimeout(longPressTimerRef.current)
  }, [])

  // Handle carousel selection change
  const onSelect = useCallback(() => {
    if (!emblaApi) return
    setSelectedIndex(emblaApi.selectedScrollSnap())
  }, [emblaApi, setSelectedIndex])

  // Handle settle - start 1s auto-play timer
  const onSettle = useCallback(() => {
    if (!emblaApi) return
    
    const currentIndex = emblaApi.selectedScrollSnap()
    const currentItem = displayItemsRef.current[currentIndex]
    
    clearTimeout(playTimerRef.current)
    
    // If this item is already playing, we're done
    if (isItemPlaying(currentItem)) {
      setPendingItem(null)
      userInteractingRef.current = false
      return
    }
    
    // Set pending item (UI shows this item's info) and start 1s timer
    setPendingItem(currentItem)
    
    playTimerRef.current = setTimeout(() => {
      if (isItemPlaying(currentItem)) {
        setPendingItem(null)
        userInteractingRef.current = false
        return
      }
      
      lastPlayedByUsRef.current = currentItem?.uri
      onSlideClick(currentItem, currentIndex)
      setPendingItem(null)
      
      // Allow sync again after play propagates
      setTimeout(() => {
        userInteractingRef.current = false
      }, 500)
    }, 1000)
  }, [emblaApi, isItemPlaying, onSlideClick, setPendingItem])

  // Subscribe to carousel events
  useEffect(() => {
    if (!emblaApi) return
    
    const onPointerDown = () => {
      pointerDownSnapRef.current = emblaApi.selectedScrollSnap()
      userInteractingRef.current = true
      clearTimeout(playTimerRef.current)
    }
    
    const onPointerUp = () => {
      const snapAtUp = emblaApi.selectedScrollSnap()
      // If snap didn't change, it was a tap - toggle play/pause
      if (snapAtUp === pointerDownSnapRef.current) {
        onTogglePlayPause()
      }
    }
    
    emblaApi.on('select', onSelect)
    emblaApi.on('settle', onSettle)
    emblaApi.on('pointerDown', onPointerDown)
    emblaApi.on('pointerUp', onPointerUp)
    onSelect()
    
    return () => {
      emblaApi.off('select', onSelect)
      emblaApi.off('settle', onSettle)
      emblaApi.off('pointerDown', onPointerDown)
      emblaApi.off('pointerUp', onPointerUp)
      clearTimeout(playTimerRef.current)
    }
  }, [emblaApi, onSelect, onSettle, onTogglePlayPause])

  // Sync carousel to playing context (external changes only)
  useEffect(() => {
    if (!emblaApi || displayItems.length === 0 || isSyncingRef.current) return
    if (userInteractingRef.current || pendingItem) return
    
    const contextUri = nowPlaying?.context?.uri
    const trackUri = nowPlaying?.track?.uri
    const playingKey = contextUri || trackUri
    
    if (!playingKey) {
      lastSyncedContextRef.current = null
      return
    }
    
    // Skip sync for item we just started
    if (playingKey === lastPlayedByUsRef.current) {
      lastPlayedByUsRef.current = null
      return
    }
    
    if (playingKey === lastSyncedContextRef.current) return
    
    const playingIndex = findPlayingIndex(displayItems, contextUri, trackUri)
    if (playingIndex === -1) return
    
    lastSyncedContextRef.current = playingKey
    
    if (emblaApi.selectedScrollSnap() === playingIndex) return
    
    isSyncingRef.current = true
    emblaApi.scrollTo(playingIndex)
    setTimeout(() => { isSyncingRef.current = false }, 500)
  }, [emblaApi, nowPlaying?.context?.uri, nowPlaying?.track?.uri, displayItems, findPlayingIndex, pendingItem])

  // Reinitialize when displayItems count changes
  useEffect(() => {
    if (!emblaApi || displayItems.length === 0) return
    
    const contextUri = nowPlaying?.context?.uri
    const trackUri = nowPlaying?.track?.uri
    const targetIndex = findPlayingIndex(displayItems, contextUri, trackUri)
    
    isSyncingRef.current = true
    emblaApi.reInit()
    
    const scrollTarget = targetIndex !== -1 ? targetIndex : selectedIndex
    if (scrollTarget >= 0 && scrollTarget < displayItems.length) {
      emblaApi.scrollTo(scrollTarget, true)
      if (targetIndex !== -1) {
        lastSyncedContextRef.current = contextUri || trackUri
      }
    }
    
    setTimeout(() => { isSyncingRef.current = false }, 100)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [emblaApi, displayItems.length])

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
