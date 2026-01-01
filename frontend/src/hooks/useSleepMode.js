import { useState, useEffect, useRef } from 'react'
import { api } from '../api'

export function useSleepMode(isPlaying, timeout = 2 * 60 * 1000) {
  const [isScreenOff, setIsScreenOff] = useState(false)
  const sleepTimerRef = useRef(null)
  const isPlayingRef = useRef(false)
  const isScreenOffRef = useRef(false)

  // Keep playing ref in sync
  useEffect(() => {
    isPlayingRef.current = isPlaying || false
  }, [isPlaying])

  // Wake when music starts
  useEffect(() => {
    if (isPlaying && isScreenOffRef.current) {
      isScreenOffRef.current = false
      setIsScreenOff(false)
      api.screenOn()
    }
  }, [isPlaying])

  // Sleep timer and activity handling
  useEffect(() => {
    const wakeUp = () => {
      if (isScreenOffRef.current) {
        isScreenOffRef.current = false
        setIsScreenOff(false)
        api.screenOn()
      }
    }

    const startSleepTimer = () => {
      clearTimeout(sleepTimerRef.current)
      sleepTimerRef.current = setTimeout(() => {
        if (!isPlayingRef.current && !isScreenOffRef.current) {
          isScreenOffRef.current = true
          setIsScreenOff(true)
          api.screenOff()
        }
      }, timeout)
    }

    const handleActivity = () => {
      wakeUp()
      startSleepTimer()
    }

    window.addEventListener('touchstart', handleActivity)
    window.addEventListener('mousedown', handleActivity)
    startSleepTimer()

    return () => {
      window.removeEventListener('touchstart', handleActivity)
      window.removeEventListener('mousedown', handleActivity)
      clearTimeout(sleepTimerRef.current)
    }
  }, [timeout])

  return isScreenOff
}

