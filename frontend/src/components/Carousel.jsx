import { useRef, useEffect, useCallback } from 'react'
import useEmblaCarousel from 'embla-carousel-react'
import { getImageUrl } from '../api'

const Carousel = ({ 
  displayItems, 
  selectedIndex, 
  setSelectedIndex,
  nowPlaying,
  tempItem,
  deleteMode,
  setDeleteMode,
  deleting,
  saving,
  onSlideClick,
  onSaveContext,
  onDeleteItem,
  isItemPlaying,
  findPlayingIndex
}) => {
  const [emblaRef, emblaApi] = useEmblaCarousel({
    loop: false,
    align: 'center',
    containScroll: false,
    skipSnaps: true,
    dragFree: false,
    watchDrag: true,
    watchResize: true,
  })

  // Refs for carousel state
  const isSyncingRef = useRef(false)
  const lastSyncedContextRef = useRef(null)
  const displayItemsRef = useRef(displayItems)
  const longPressTimerRef = useRef(null)
  
  useEffect(() => {
    displayItemsRef.current = displayItems
  }, [displayItems])

  // Handle carousel selection change
  const onSelect = useCallback(() => {
    if (!emblaApi) return
    setSelectedIndex(emblaApi.selectedScrollSnap())
  }, [emblaApi, setSelectedIndex])

  // Handle settle (carousel stops) - play selected item
  const onSettle = useCallback(() => {
    if (!emblaApi || isSyncingRef.current) return
    const item = displayItemsRef.current[emblaApi.selectedScrollSnap()]
    onSlideClick(item, emblaApi.selectedScrollSnap())
  }, [emblaApi, onSlideClick])

  // Subscribe to carousel events
  useEffect(() => {
    if (!emblaApi) return
    
    emblaApi.on('select', onSelect)
    emblaApi.on('settle', onSettle)
    onSelect()
    
    return () => {
      emblaApi.off('select', onSelect)
      emblaApi.off('settle', onSettle)
    }
  }, [emblaApi, onSelect, onSettle])

  // Sync carousel to playing context (only when context CHANGES)
  useEffect(() => {
    if (!emblaApi || displayItems.length === 0 || isSyncingRef.current) return
    
    const contextUri = nowPlaying?.context?.uri
    const trackUri = nowPlaying?.track?.uri
    const playingKey = contextUri || trackUri
    
    if (!playingKey) {
      lastSyncedContextRef.current = null
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
  }, [emblaApi, nowPlaying?.context?.uri, nowPlaying?.track?.uri, displayItems, findPlayingIndex])

  // Reinitialize carousel when displayItems count changes
  useEffect(() => {
    if (!emblaApi || displayItems.length === 0) return
    
    const contextUri = nowPlaying?.context?.uri
    const trackUri = nowPlaying?.track?.uri
    const targetIndex = findPlayingIndex(displayItems, contextUri, trackUri)
    
    isSyncingRef.current = true
    emblaApi.reInit()
    
    // Scroll to playing item if found, otherwise scroll to selectedIndex
    const scrollTarget = targetIndex !== -1 ? targetIndex : selectedIndex
    if (scrollTarget >= 0 && scrollTarget < displayItems.length) {
      emblaApi.scrollTo(scrollTarget, true)
      if (targetIndex !== -1) {
        lastSyncedContextRef.current = contextUri || trackUri
      }
    }
    
    setTimeout(() => { isSyncingRef.current = false }, 100)
  }, [emblaApi, displayItems.length, selectedIndex, nowPlaying?.context?.uri, nowPlaying?.track?.uri, findPlayingIndex])

  // Long press handlers for delete mode
  const handlePressStart = (item) => {
    if (item.isTemp) return
    longPressTimerRef.current = setTimeout(() => {
      setDeleteMode(item.id)
    }, 1000)
  }

  const handlePressEnd = () => {
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current)
      longPressTimerRef.current = null
    }
  }

  // Handle slide click
  const handleSlideClick = (index) => {
    if (!emblaApi) return
    emblaApi.scrollTo(index)
    const item = displayItemsRef.current[index]
    onSlideClick(item, index)
  }

  // Render album cover
  const renderCover = (item) => {
    const isItemPlaylist = item.uri?.includes('playlist') || item.type === 'playlist'
    
    if (isItemPlaylist) {
      const covers = item.images || []
      return (
        <div className="composite-cover">
          {[0, 1, 2, 3].map((i) => {
            const img = covers[i]
            if (img) {
              return <img key={i} src={getImageUrl(img)} alt="" />
            }
            return <div key={i} className="composite-empty" />
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
                onClick={() => !isInDeleteMode && handleSlideClick(index)}
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
                  {/* Delete button for catalog items in delete mode */}
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

