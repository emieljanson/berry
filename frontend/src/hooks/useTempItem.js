import { useMemo, useCallback } from 'react'

// Debug logging (set to true for troubleshooting)
const DEBUG = false
const log = (...args) => DEBUG && console.log('[useTempItem]', ...args)

export function useTempItem(catalogItems, nowPlaying) {
  // Derived via useMemo - no useState needed
  const tempItem = useMemo(() => {
    const contextUri = nowPlaying?.context?.uri
    const trackUri = nowPlaying?.track?.uri
    
    if (!contextUri) return null
    
    // Check if already in catalog
    const inCatalog = catalogItems.some(item => 
      item.uri === contextUri || item.currentTrack?.uri === trackUri
    )
    if (inCatalog) return null
    
    // Build temp item
    const isPlaylist = contextUri.includes('playlist')
    return {
      id: 'temp',
      type: isPlaylist ? 'playlist' : 'album',
      uri: contextUri,
      name: nowPlaying.track ? (isPlaylist ? 'Playlist' : nowPlaying.track.album) : 'Loading...',
      artist: nowPlaying.track?.artist || '',
      image: nowPlaying.track?.albumCover || null,
      images: isPlaylist ? (nowPlaying.context?.covers || []) : null,
      isTemp: true
    }
  }, [catalogItems, nowPlaying])

  // Combine catalog items with optional temp item
  const displayItems = useMemo(() => 
    tempItem ? [...catalogItems, tempItem] : catalogItems
  , [catalogItems, tempItem])

  // Find index of item matching current playback
  const findPlayingIndex = useCallback((items, contextUri, trackUri, albumName) => {
    if (!contextUri && !trackUri && !albumName) {
      log('findPlayingIndex: no context, track URI, or album name')
      return -1
    }
    
    // First try: direct context URI match
    let index = items.findIndex(item => item.uri === contextUri)
    log('findPlayingIndex:', 'contextUri:', contextUri, 'trackUri:', trackUri, 'albumName:', albumName, 'directMatch:', index, 'itemsLength:', items.length)
    
    // Second try: match by saved track progress
    if (index === -1 && trackUri) {
      index = items.findIndex(item => item.currentTrack?.uri === trackUri)
      log('findPlayingIndex: tried currentTrack match:', index)
    }
    
    // Third try: match by album name (for albums)
    if (index === -1 && albumName) {
      index = items.findIndex(item => 
        item.type === 'album' && (item.album === albumName || item.name === albumName)
      )
      log('findPlayingIndex: tried album name match:', index)
    }
    
    // Fourth try: tempItem match
    if (index === -1 && tempItem && (tempItem.uri === contextUri || tempItem.uri === trackUri)) {
      index = items.length - 1
      log('findPlayingIndex: matched tempItem at index:', index)
    }
    
    return index
  }, [tempItem])

  return { tempItem, displayItems, findPlayingIndex }
}

