"""
Bluetooth Manager - BT device discovery, pairing, and audio routing.

Uses bluetoothctl (subprocess) for BlueZ interaction and pactl for PipeWire
audio sink switching. Follows the existing pattern in setup_menu.py (nmcli).
"""
import re
import sys
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, List, Callable

from ..config import WM8960_SINK, BT_MONITOR_INTERVAL, BT_SCAN_DURATION

logger = logging.getLogger(__name__)

# Audio Sink UUID — devices advertising this support A2DP audio output
AUDIO_SINK_UUID = '0000110b-0000-1000-8000-00805f9b34fb'

# Regex to detect MAC-address-like names (skip these in the UI)
_MAC_NAME_RE = re.compile(r'^([0-9A-Fa-f]{2}[-:]){3,}[0-9A-Fa-f]{2}$|^([0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}$')


def _is_mac_like(name: str) -> bool:
    return bool(_MAC_NAME_RE.match(name))


@dataclass
class BluetoothDevice:
    mac: str
    name: str
    paired: bool = False
    connected: bool = False
    is_audio: bool = False


class BluetoothManager:
    """Manages Bluetooth device discovery, pairing, and PipeWire audio routing."""

    def __init__(
        self,
        settings,
        on_toast: Callable[[str], None],
        on_invalidate: Callable[[], None],
        on_audio_changed: Callable[[bool], None],  # True = BT active, False = speaker
    ):
        self._settings = settings
        self._on_toast = on_toast
        self._on_invalidate = on_invalidate
        self._on_audio_changed = on_audio_changed

        self._lock = threading.Lock()
        self._paired_devices: List[BluetoothDevice] = []
        self._discovered_devices: List[BluetoothDevice] = []
        self._connected_device: Optional[BluetoothDevice] = None
        self._audio_active: bool = False  # True = audio routed to BT

        self._scan_process: Optional[subprocess.Popen] = None
        self._scanning: bool = False

        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reconnect_cooldown: int = 0

    # ------------------------------------------------------------------
    # Public state (thread-safe reads)
    # ------------------------------------------------------------------

    @property
    def paired_devices(self) -> List[BluetoothDevice]:
        with self._lock:
            return list(self._paired_devices)

    @property
    def discovered_devices(self) -> List[BluetoothDevice]:
        with self._lock:
            return list(self._discovered_devices)

    @property
    def connected_device(self) -> Optional[BluetoothDevice]:
        with self._lock:
            return self._connected_device

    @property
    def audio_active(self) -> bool:
        with self._lock:
            return self._audio_active

    @property
    def scanning(self) -> bool:
        with self._lock:
            return self._scanning

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_monitoring(self):
        """Start background thread that polls BT connection state."""
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info('Bluetooth: monitoring started')

    def stop(self):
        """Stop background threads and scanning."""
        self._stop_event.set()
        self.stop_scan()

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def start_scan(self):
        """Start BT discovery in background. Restarts BT service to fix stuck adapter."""
        def _do_scan():
            logger.info('Bluetooth: restarting BT service before scan')
            try:
                subprocess.run(
                    ['sudo', 'systemctl', 'restart', 'bluetooth'],
                    timeout=10, capture_output=True,
                )
                time.sleep(2)
                subprocess.run(
                    ['bluetoothctl', 'power', 'on'],
                    timeout=5, capture_output=True,
                )
                time.sleep(1)
            except Exception as e:
                logger.warning(f'Bluetooth: service restart failed: {e}')

            with self._lock:
                self._scanning = True
            self._on_invalidate()

            try:
                proc = subprocess.Popen(
                    ['bluetoothctl', '--timeout', str(int(BT_SCAN_DURATION)), 'scan', 'on'],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True,
                )
                with self._lock:
                    self._scan_process = proc

                discovered: dict[str, BluetoothDevice] = {}

                for line in proc.stdout:
                    line = line.strip()
                    # NEW Device <MAC> <Name>
                    m = re.match(r'\[NEW\]\s+Device\s+([0-9A-Fa-f:]{17})\s+(.*)', line)
                    if m:
                        mac, name = m.group(1), m.group(2).strip()
                        if not _is_mac_like(name):
                            discovered[mac] = BluetoothDevice(mac=mac, name=name)
                    # CHG Device <MAC> Name: <Name>
                    m = re.match(r'\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+Name:\s+(.*)', line)
                    if m:
                        mac, name = m.group(1), m.group(2).strip()
                        if not _is_mac_like(name):
                            if mac in discovered:
                                discovered[mac].name = name
                            else:
                                discovered[mac] = BluetoothDevice(mac=mac, name=name)

                    # Update UI periodically
                    if discovered:
                        self._update_discovered(discovered)

            except Exception as e:
                logger.warning(f'Bluetooth: scan error: {e}')
            finally:
                with self._lock:
                    self._scan_process = None
                    self._scanning = False
                self._on_invalidate()
                # Refresh paired list and check audio devices after scan
                self._check_audio_devices(discovered)
                self.refresh_paired()

        threading.Thread(target=_do_scan, daemon=True).start()

    def stop_scan(self):
        """Stop BT discovery."""
        with self._lock:
            proc = self._scan_process
            self._scan_process = None
            self._scanning = False
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            subprocess.run(['bluetoothctl', 'scan', 'off'], timeout=3, capture_output=True)
        except Exception:
            pass

    def _update_discovered(self, discovered: dict):
        """Update discovered list (excludes already-paired devices)."""
        paired_macs = {d.mac for d in self._paired_devices}
        new_list = [d for mac, d in discovered.items() if mac not in paired_macs]
        with self._lock:
            self._discovered_devices = new_list
        self._on_invalidate()

    def _check_audio_devices(self, discovered: dict):
        """Filter discovered devices to only audio-capable ones via bluetoothctl info."""
        if sys.platform != 'linux':
            return
        audio_macs = set()
        paired_macs = {d.mac for d in self._paired_devices}
        for mac in list(discovered.keys()):
            if mac in paired_macs:
                continue
            try:
                result = subprocess.run(
                    ['bluetoothctl', 'info', mac],
                    capture_output=True, text=True, timeout=5,
                )
                info = result.stdout
                if AUDIO_SINK_UUID in info or 'audio-headset' in info or 'audio-headphones' in info:
                    audio_macs.add(mac)
            except Exception:
                pass

        with self._lock:
            self._discovered_devices = [
                d for d in self._discovered_devices
                if d.mac in audio_macs
            ]
        self._on_invalidate()

    # ------------------------------------------------------------------
    # Paired device management
    # ------------------------------------------------------------------

    def refresh_paired(self):
        """Refresh list of paired devices and their connection state."""
        if sys.platform != 'linux':
            return
        try:
            result = subprocess.run(
                ['bluetoothctl', 'devices', 'Paired'],
                capture_output=True, text=True, timeout=5,
            )
            devices = []
            for line in result.stdout.strip().splitlines():
                m = re.match(r'Device\s+([0-9A-Fa-f:]{17})\s+(.*)', line)
                if not m:
                    continue
                mac, name = m.group(1), m.group(2).strip()
                info = self._get_device_info(mac)
                connected = 'Connected: yes' in info
                is_audio = AUDIO_SINK_UUID in info or 'audio-headset' in info or 'audio-headphones' in info
                devices.append(BluetoothDevice(
                    mac=mac,
                    name=name,
                    paired=True,
                    connected=connected,
                    is_audio=is_audio,
                ))
            with self._lock:
                self._paired_devices = devices
            logger.debug(f'Bluetooth: {len(devices)} paired device(s)')
        except Exception as e:
            logger.warning(f'Bluetooth: refresh_paired error: {e}')

    def _get_device_info(self, mac: str) -> str:
        try:
            result = subprocess.run(
                ['bluetoothctl', 'info', mac],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout
        except Exception:
            return ''

    # ------------------------------------------------------------------
    # Connect / disconnect / pair
    # ------------------------------------------------------------------

    def connect(self, mac: str):
        """Connect to a paired device."""
        def _do():
            logger.info(f'Bluetooth: connecting to {mac}')
            self._on_toast('Verbinden...')

            # Wait for adapter to be powered (scan may be restarting BT service)
            self._wait_adapter_ready()

            try:
                result = subprocess.run(
                    ['bluetoothctl', 'connect', mac],
                    capture_output=True, text=True, timeout=15,
                )
                if 'Connection successful' in result.stdout or 'Connected: yes' in result.stdout:
                    logger.info(f'Bluetooth: connected to {mac}')
                    self.refresh_paired()
                    with self._lock:
                        self._connected_device = next(
                            (d for d in self._paired_devices if d.mac == mac and d.connected),
                            self._connected_device,
                        )
                    self._on_invalidate()
                else:
                    logger.warning(f'Bluetooth: connect failed: {result.stdout.strip()}')
                    self._on_toast('Verbinden mislukt')
                    self._on_invalidate()
            except Exception as e:
                logger.warning(f'Bluetooth: connect error: {e}')
                self._on_toast('Verbinden mislukt')
                self._on_invalidate()
        threading.Thread(target=_do, daemon=True).start()

    def disconnect(self):
        """Disconnect the currently connected device."""
        dev = self.connected_device
        if not dev:
            return

        def _do():
            logger.info(f'Bluetooth: disconnecting {dev.mac}')
            # Switch audio back to speaker before disconnecting
            if self.audio_active:
                self.switch_to_speaker()
            try:
                subprocess.run(
                    ['bluetoothctl', 'disconnect', dev.mac],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception as e:
                logger.warning(f'Bluetooth: disconnect error: {e}')
            with self._lock:
                self._connected_device = None
            self.refresh_paired()
            self._on_invalidate()
        threading.Thread(target=_do, daemon=True).start()

    def pair_and_connect(self, mac: str, name: str):
        """Pair, trust, and connect a new device."""
        def _do():
            logger.info(f'Bluetooth: pairing with {mac} ({name})')
            self._on_toast(f'Koppelen met {name}...')
            self._wait_adapter_ready()
            try:
                subprocess.run(['bluetoothctl', 'pair', mac], capture_output=True, text=True, timeout=30)
                subprocess.run(['bluetoothctl', 'trust', mac], capture_output=True, text=True, timeout=5)
                result = subprocess.run(
                    ['bluetoothctl', 'connect', mac],
                    capture_output=True, text=True, timeout=15,
                )
                if 'Connection successful' in result.stdout or 'Connected: yes' in result.stdout:
                    logger.info(f'Bluetooth: paired and connected to {mac}')
                    self.refresh_paired()
                    with self._lock:
                        self._connected_device = next(
                            (d for d in self._paired_devices if d.mac == mac and d.connected),
                            self._connected_device,
                        )
                    self._on_invalidate()
                else:
                    logger.warning(f'Bluetooth: pair+connect failed: {result.stdout.strip()}')
                    self._on_toast('Koppelen mislukt')
            except Exception as e:
                logger.warning(f'Bluetooth: pair_and_connect error: {e}')
                self._on_toast('Koppelen mislukt')
        threading.Thread(target=_do, daemon=True).start()

    def forget(self, mac: str):
        """Remove a paired device."""
        def _do():
            try:
                subprocess.run(['bluetoothctl', 'remove', mac], capture_output=True, timeout=5)
                self.refresh_paired()
                self._on_invalidate()
            except Exception as e:
                logger.warning(f'Bluetooth: forget error: {e}')
        threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------------------
    # Audio routing
    # ------------------------------------------------------------------

    def switch_to_bluetooth(self):
        """Route audio to connected BT device via pactl."""
        dev = self.connected_device
        if not dev:
            logger.warning('Bluetooth: switch_to_bluetooth called with no connected device')
            return

        def _do():
            # Wait up to 5s for PipeWire to create the BT sink
            bt_sink = self._find_bt_sink(retries=5)
            if not bt_sink:
                logger.warning('Bluetooth: BT sink not found in PipeWire')
                self._on_toast('Koptelefoon niet beschikbaar')
                return

            stream_id = self._find_librespot_stream()
            if stream_id:
                try:
                    subprocess.run(
                        ['pactl', 'move-sink-input', stream_id, bt_sink],
                        capture_output=True, timeout=5,
                    )
                    logger.info(f'Bluetooth: stream {stream_id} → {bt_sink}')
                except Exception as e:
                    logger.warning(f'Bluetooth: move-sink-input error: {e}')

            # Store desired sink for when stream appears later (e.g. on play)
            with self._lock:
                self._audio_active = True
                self._desired_sink = bt_sink
            self._settings.set_last_bt_device_mac(dev.mac)
            self._on_audio_changed(True)
            self._on_invalidate()

        threading.Thread(target=_do, daemon=True).start()

    def switch_to_speaker(self):
        """Route audio back to WM8960 speaker."""
        def _do():
            stream_id = self._find_librespot_stream()
            if stream_id:
                try:
                    subprocess.run(
                        ['pactl', 'move-sink-input', stream_id, WM8960_SINK],
                        capture_output=True, timeout=5,
                    )
                    logger.info(f'Bluetooth: stream {stream_id} → speaker')
                except Exception as e:
                    logger.warning(f'Bluetooth: move-sink-input error: {e}')

            with self._lock:
                self._audio_active = False
                self._desired_sink = None
            self._on_audio_changed(False)
            self._on_invalidate()

        threading.Thread(target=_do, daemon=True).start()

    def toggle_audio(self):
        """Toggle audio between BT headphone and speaker."""
        if self.audio_active:
            self.switch_to_speaker()
        else:
            self.switch_to_bluetooth()

    def set_volume(self, level: int):
        """Set volume on the active BT sink via pactl (0-100)."""
        with self._lock:
            desired = getattr(self, '_desired_sink', None)
            active = self._audio_active
        if not active or not desired:
            return
        try:
            subprocess.run(
                ['pactl', 'set-sink-volume', desired, f'{level}%'],
                capture_output=True, timeout=5,
            )
            logger.debug(f'Bluetooth: set sink volume {level}% on {desired}')
        except Exception as e:
            logger.warning(f'Bluetooth: set volume error: {e}')

    def ensure_stream_on_desired_sink(self):
        """Called when playback starts — move stream to desired sink if set."""
        with self._lock:
            desired = getattr(self, '_desired_sink', None)
            active = self._audio_active
        if active and desired:
            stream_id = self._find_librespot_stream()
            if stream_id:
                try:
                    subprocess.run(
                        ['pactl', 'move-sink-input', stream_id, desired],
                        capture_output=True, timeout=5,
                    )
                except Exception:
                    pass

    def _find_bt_sink(self, retries: int = 1) -> Optional[str]:
        """Find the active PipeWire BT sink name (not WM8960, not bcm2835)."""
        for attempt in range(retries):
            try:
                result = subprocess.run(
                    ['pactl', 'list', 'sinks', 'short'],
                    capture_output=True, text=True, timeout=5,
                )
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        sink_name = parts[1]
                        if ('bluez' in sink_name or
                                (WM8960_SINK not in sink_name and 'mailbox' not in sink_name)):
                            if 'bluez' in sink_name:
                                return sink_name
            except Exception as e:
                logger.warning(f'Bluetooth: list sinks error: {e}')
            if attempt < retries - 1:
                time.sleep(1)
        return None

    def _find_librespot_stream(self) -> Optional[str]:
        """Find the active PipeWire sink-input ID for go-librespot."""
        try:
            result = subprocess.run(
                ['pactl', 'list', 'sink-inputs', 'short'],
                capture_output=True, text=True, timeout=5,
            )
            # Return first active stream (go-librespot is the only audio source)
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts:
                    return parts[0]
        except Exception as e:
            logger.warning(f'Bluetooth: list sink-inputs error: {e}')
        return None

    # ------------------------------------------------------------------
    # Adapter helpers
    # ------------------------------------------------------------------

    def _wait_adapter_ready(self, timeout: float = 15.0):
        """Block until the BT adapter is powered on (scan may be restarting the service)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(
                    ['bluetoothctl', 'show'],
                    capture_output=True, text=True, timeout=5,
                )
                if 'Powered: yes' in result.stdout:
                    return
            except Exception:
                pass
            time.sleep(1)
        logger.warning('Bluetooth: adapter not ready after %.0fs', timeout)

    # ------------------------------------------------------------------
    # Background monitoring
    # ------------------------------------------------------------------

    def _monitor_loop(self):
        """Poll paired device connection state and handle audio routing."""
        # Initialize desired_sink
        self._desired_sink = None

        # Initial refresh
        self.refresh_paired()

        while not self._stop_event.wait(BT_MONITOR_INTERVAL):
            self._poll_connection_state()

    def _poll_connection_state(self):
        """Check if any paired audio device connected or disconnected."""
        if sys.platform != 'linux':
            return

        try:
            result = subprocess.run(
                ['bluetoothctl', 'devices', 'Connected'],
                capture_output=True, text=True, timeout=5,
            )
            connected_macs = set()
            for line in result.stdout.strip().splitlines():
                m = re.match(r'Device\s+([0-9A-Fa-f:]{17})', line)
                if m:
                    connected_macs.add(m.group(1))

            with self._lock:
                prev_connected = self._connected_device
                prev_mac = prev_connected.mac if prev_connected else None

            # Check paired audio devices
            self.refresh_paired()
            paired = self.paired_devices
            new_connected = next(
                (d for d in paired if d.connected and d.is_audio), None
            )
            new_mac = new_connected.mac if new_connected else None

            if new_mac != prev_mac:
                if new_connected and not prev_mac:
                    # Device connected
                    logger.info(f'Bluetooth: {new_connected.name} connected')
                    with self._lock:
                        self._connected_device = new_connected
                    self._on_toast(f'{new_connected.name} verbonden')
                    self._on_invalidate()
                elif not new_connected and prev_mac:
                    # Device disconnected
                    logger.info(f'Bluetooth: {prev_connected.name} disconnected')
                    with self._lock:
                        self._connected_device = None
                        was_active = self._audio_active
                        self._audio_active = False
                        self._desired_sink = None
                    if was_active:
                        # PipeWire auto-reverts to default sink on BT disconnect
                        self._on_audio_changed(False)
                    self._on_toast(f'{prev_connected.name} losgekoppeld')
                    self._on_invalidate()
                else:
                    with self._lock:
                        self._connected_device = new_connected
                    self._on_invalidate()
            elif not new_connected:
                # No audio device connected — try auto-reconnect
                self._try_auto_reconnect(paired)

        except Exception as e:
            logger.debug(f'Bluetooth: monitor poll error: {e}')

    def _try_auto_reconnect(self, paired: list):
        """Try to reconnect a trusted audio device that's in range."""
        cooldown = getattr(self, '_reconnect_cooldown', 0)
        if cooldown > 0:
            self._reconnect_cooldown = cooldown - 1
            return

        for dev in paired:
            if not dev.is_audio:
                continue
            # Check if device is in range (RSSI present in bluetoothctl info)
            info = self._get_device_info(dev.mac)
            if 'RSSI' not in info:
                continue

            logger.info(f'Bluetooth: auto-reconnecting to {dev.name}')
            try:
                result = subprocess.run(
                    ['bluetoothctl', 'connect', dev.mac],
                    capture_output=True, text=True, timeout=15,
                )
                if 'Connection successful' in result.stdout or 'Connected: yes' in result.stdout:
                    self.refresh_paired()
                    with self._lock:
                        self._connected_device = next(
                            (d for d in self._paired_devices if d.mac == dev.mac and d.connected),
                            None,
                        )
                    if self._connected_device:
                        logger.info(f'Bluetooth: auto-reconnected to {dev.name}')
                        self._on_toast(f'{dev.name} verbonden')
                        self._on_invalidate()
                    return
                else:
                    logger.info(f'Bluetooth: auto-reconnect failed: {result.stdout.strip()}')
                    self._reconnect_cooldown = 3
            except Exception as e:
                logger.warning(f'Bluetooth: auto-reconnect error: {e}')
                self._reconnect_cooldown = 3
            return  # Max 1 attempt per cycle
