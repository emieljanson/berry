// Sleep overlay - fades to black when inactive
const SleepOverlay = ({ isActive }) => (
  <div className={`sleep-overlay ${isActive ? 'active' : ''}`} />
)

export default SleepOverlay

