import { useMemo, useCallback } from 'react'

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
  const findPlayingIndex = useCallback((items, contextUri, trackUri) => {
    if (!contextUri && !trackUri) return -1
    
    let index = items.findIndex(item => item.uri === contextUri)
    
    if (index === -1 && trackUri) {
      index = items.findIndex(item => item.currentTrack?.uri === trackUri)
    }
    
    if (index === -1 && tempItem && (tempItem.uri === contextUri || tempItem.uri === trackUri)) {
      index = items.length - 1
    }
    
    return index
  }, [tempItem])

  return { tempItem, displayItems, findPlayingIndex }
}

