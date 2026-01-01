import { useRef, useEffect } from 'react'

// Format milliseconds to mm:ss
const formatTime = (ms) => {
  if (!ms) return '0:00'
  const seconds = Math.floor(ms / 1000)
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

// Custom hook for progress bar animation logic
export const useProgressAnimation = (track, isPlaying) => {
  const progress = track?.duration ? (track.position / track.duration) * 100 : 0
  const lastPositionRef = useRef(track?.position || 0)
  
  const position = track?.position || 0
  const diff = Math.abs(position - lastPositionRef.current)
  
  // Animate only if playing AND small increment (< 1.5s = normal playback)
  const shouldAnimate = isPlaying && diff > 0 && diff < 1500
  
  // Update ref after render
  useEffect(() => {
    lastPositionRef.current = position
  })
  
  return {
    progress,
    shouldAnimate,
    currentTime: formatTime(track?.position),
    totalTime: formatTime(track?.duration)
  }
}

// Playback controls component - reusable for normal and compact views
export const PlaybackControls = ({ isPlaying, onPrev, onToggle, onNext, compact }) => (
  <div className={`controls ${compact ? 'compact' : ''}`}>
    <button className={`control-btn ${compact ? 'small' : ''}`} onClick={onPrev} aria-label="Previous">
      <svg viewBox="0 0 24 24" fill="currentColor">
        <rect x="6" y="5" width="2.5" height="14" rx="1.25"/>
        <path d="M10.5 11.3c-.3.2-.3.6 0 .9l7 4.8c.4.3 1 0 1-.5V7.5c0-.5-.6-.8-1-.5l-7 4.3z"/>
      </svg>
    </button>
    
    <button 
      className={`control-btn play-btn ${compact ? 'small' : ''}`}
      onClick={onToggle}
      aria-label={isPlaying ? 'Pause' : 'Play'}
    >
      {isPlaying ? (
        <svg viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="5" width="4" height="14" rx="1.5"/>
          <rect x="14" y="5" width="4" height="14" rx="1.5"/>
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M8 6.82c0-.87.96-1.4 1.7-.94l8.56 5.18c.66.4.66 1.38 0 1.78l-8.56 5.18c-.74.46-1.7-.07-1.7-.94V6.82z"/>
        </svg>
      )}
    </button>
    
    <button className={`control-btn ${compact ? 'small' : ''}`} onClick={onNext} aria-label="Next">
      <svg viewBox="0 0 24 24" fill="currentColor">
        <path d="M6.5 7.5c0-.5.6-.8 1-.5l7 4.3c.3.2.3.6 0 .9l-7 4.8c-.4.3-1 0-1-.5V7.5z"/>
        <rect x="15.5" y="5" width="2.5" height="14" rx="1.25"/>
      </svg>
    </button>
  </div>
)

// Progress bar component
export const ProgressBar = ({ progress, shouldAnimate, currentTime, totalTime }) => (
  <div className="progress-container">
    <span className="time-current">{currentTime}</span>
    <div className="progress-bar">
      <div 
        className="progress-fill" 
        style={{ 
          width: `${progress}%`,
          transition: shouldAnimate ? 'width 1s linear' : 'none'
        }} 
      />
    </div>
    <span className="time-total">{totalTime}</span>
  </div>
)

