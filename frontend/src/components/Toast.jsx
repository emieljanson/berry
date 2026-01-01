// Toast notification component
const Toast = ({ toast }) => {
  if (!toast) return null
  
  return (
    <div className={`toast ${toast.visible ? 'visible' : ''}`}>
      {toast.message}
    </div>
  )
}

export default Toast

