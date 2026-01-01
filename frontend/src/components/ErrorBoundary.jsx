import React from 'react'

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error, errorInfo) {
    console.error('Berry error:', error, errorInfo)
  }

  handleReload = () => {
    window.location.reload()
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-screen" onClick={this.handleReload}>
          <div className="error-content">
            <div className="error-icon">🍓</div>
            <h1>Something went wrong</h1>
            <p>Tap anywhere to reload</p>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}

export default ErrorBoundary

