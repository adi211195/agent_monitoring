"""
webrtc_streamer.py - WebRTC screen capture streamer untuk agent monitoring.

Mengirim video screen langsung ke browser admin via WebRTC (P2P).
Server hanya dipakai untuk signaling (SDP + ICE), frame tidak lewat server.

DataChannel 'remote-input' menerima mouse/keyboard dari browser admin
dan meneruskan ke RemoteControlAgent untuk dieksekusi sebagai native input.
"""

import asyncio
import time
import threading
import logging

logger = logging.getLogger(__name__)

try:
    import av
    import mss
    import numpy as np
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    AIORTC_AVAILABLE = True
except ImportError:
    AIORTC_AVAILABLE = False
    logger.warning("[WebRTC] aiortc tidak tersedia. Jalankan: pip install aiortc")


class ScreenCaptureTrack(VideoStreamTrack):
    """
    Video track yang capture layar dan kirim sebagai frame H.264/VP8.
    aiortc otomatis encode ke codec yang disepakati.
    """
    kind = "video"

    def __init__(self, fps: int = 15, max_width: int = 1600):
        super().__init__()
        self._fps = fps
        self._interval = 1.0 / fps
        self._last_t = 0.0
        self._sct = None
        self._max_width = max_width
        self._frame_count = 0

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        # Throttle ke target FPS
        now = time.monotonic()
        sleep = self._interval - (now - self._last_t)
        if sleep > 0:
            await asyncio.sleep(sleep)
        self._last_t = time.monotonic()

        # Init mss sekali
        if self._sct is None:
            self._sct = mss.mss()

        # Capture
        monitor = self._sct.monitors[1]
        shot = self._sct.grab(monitor)

        # Convert ke numpy array RGB
        img = np.frombuffer(shot.raw, dtype=np.uint8)
        img = img.reshape((shot.height, shot.width, 4))
        img = img[:, :, :3][:, :, ::-1].copy()  # BGRA → RGB

        # Downscale kalau terlalu besar (hemat bandwidth)
        if img.shape[1] > self._max_width:
            import cv2
            ratio = self._max_width / img.shape[1]
            new_w = self._max_width
            new_h = int(img.shape[0] * ratio)
            img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # Buat VideoFrame
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        frame.pts = pts
        frame.time_base = time_base

        self._frame_count += 1
        return frame


def fix_sdp_for_chrome(raw_sdp: str) -> str:
    """
    Reorder SDP agar kompatibel dengan Chrome:
    - ice-ufrag, ice-pwd, fingerprint, setup harus SEBELUM candidates
    - Hanya simpan fingerprint sha-256 (hapus sha-384 & sha-512)
    - Line endings CRLF
    """
    lines = raw_sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    session, media_sections, current = [], [], None
    for line in lines:
        if line.startswith("m="):
            if current is not None:
                media_sections.append(current)
            current = [line]
        elif current is None:
            session.append(line)
        else:
            current.append(line)
    if current:
        media_sections.append(current)

    def reorder(ml):
        DTLS = ("a=rtcp:", "a=ice-ufrag:", "a=ice-pwd:", "a=fingerprint:sha-256",
                "a=setup:", "a=mid:")
        m     = [l for l in ml if l.startswith("m=")]
        c     = [l for l in ml if l.startswith("c=")]
        dtls  = [l for l in ml if any(l.startswith(p) for p in DTLS)]
        cands = [l for l in ml if l.startswith("a=candidate:")]
        eoc   = ["a=end-of-candidates"] if "a=end-of-candidates" in ml else []
        skip  = set(m + c + dtls + cands + eoc)
        others = [l for l in ml
                  if l not in skip
                  and not l.startswith("a=fingerprint:")
                  and l.strip() != ""]
        return m + c + dtls + others + cands + eoc

    result = session
    for ms in media_sections:
        result = result + reorder(ms)

    return "\r\n".join(result) + "\r\n"


class WebRtcStreamer:
    """
    WebRTC streamer untuk remote desktop.

    Lifecycle:
      - start() → buat event loop thread
      - on_browser_offer() → browser kirim offer (browser sebagai offerer)
      - on_request() → agent buat offer (agent sebagai offerer, legacy)
      - on_answer() → agent terima answer dari browser
      - on_ice_candidate() → agent terima ICE candidate dari browser
      - on_stop() → tutup sesi
    """

    def __init__(self, data_sender, log_callback=None, fps: int = 15):
        self._sender = data_sender
        self._log = log_callback or (lambda msg: None)
        self._fps = fps
        self._pc = None
        self._loop = None
        self._thread = None
        self._running = False
        self._remote_ctrl = None
        self._data_channel = None
        self._screen_w = 1920
        self._screen_h = 1080

    def set_remote_ctrl(self, agent):
        """Set RemoteControlAgent untuk handle input events dari DataChannel."""
        self._remote_ctrl = agent

    def is_available(self) -> bool:
        return AIORTC_AVAILABLE

    def start(self):
        """Mulai event loop thread."""
        if self._running:
            return
        if not AIORTC_AVAILABLE:
            self._log("[WebRTC] aiortc tidak tersedia — install: pip install aiortc")
            return
        self._running = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._log("[WebRTC] Event loop started")

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def on_request(self):
        """Admin minta sesi → agent buat offer (mode legacy)."""
        if not self._loop:
            self.start()
        if not self._loop:
            return
        self._log("[WebRTC] Received request, creating offer...")
        asyncio.run_coroutine_threadsafe(self._create_offer(), self._loop)

    def on_browser_offer(self, sdp: str, sdp_type: str):
        """Browser kirim SDP offer → agent buat answer (mode utama)."""
        if not self._loop:
            self.start()
        if not self._loop:
            return
        self._log("[WebRTC] Browser offer received, creating answer...")
        asyncio.run_coroutine_threadsafe(self._create_answer(sdp, sdp_type), self._loop)

    def on_answer(self, sdp: str, sdp_type: str):
        """Agent terima SDP answer dari browser (mode legacy, agent offerer)."""
        if not self._loop or not self._pc:
            return
        asyncio.run_coroutine_threadsafe(self._set_remote_answer(sdp, sdp_type), self._loop)

    def on_ice_candidate(self, candidate: dict):
        """Agent terima ICE candidate dari browser."""
        if not self._loop or not self._pc:
            return
        asyncio.run_coroutine_threadsafe(self._add_ice_candidate(candidate), self._loop)

    def on_stop(self):
        """Tutup sesi WebRTC."""
        if self._loop and self._pc:
            asyncio.run_coroutine_threadsafe(self._close(), self._loop)

    def stop(self):
        self._running = False
        if self._loop and self._pc:
            asyncio.run_coroutine_threadsafe(self._close(), self._loop)

    async def _create_offer(self):
        """Mode legacy: agent buat offer."""
        if self._pc:
            await self._pc.close()
        self._pc = RTCPeerConnection()
        track = ScreenCaptureTrack(fps=self._fps)
        self._pc.addTrack(track)

        @self._pc.on("icecandidate")
        async def on_ice(candidate):
            if candidate:
                self._sender.send_webrtc_ice({
                    "candidate": candidate.candidate,
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                })

        @self._pc.on("connectionstatechange")
        async def on_state():
            self._log(f"[WebRTC] Connection state: {self._pc.connectionState}")

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        sdp = fix_sdp_for_chrome(self._pc.localDescription.sdp)
        self._log("[WebRTC] Offer created, sending to admin...")
        self._sender.send_webrtc_offer(sdp=sdp, sdp_type=self._pc.localDescription.type)

    async def _create_answer(self, offer_sdp: str, offer_type: str):
        """
        Mode utama: browser kirim offer, agent buat answer.
        DataChannel 'remote-input' dibuat oleh browser, agent terima di sini.
        """
        try:
            if self._pc:
                await self._pc.close()

            self._pc = RTCPeerConnection()

            # Add video track
            track = ScreenCaptureTrack(fps=self._fps)
            self._pc.addTrack(track)

            # ICE candidate callback
            @self._pc.on("icecandidate")
            async def on_ice(candidate):
                if candidate:
                    self._sender.send_webrtc_ice({
                        "candidate": candidate.candidate,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    })

            # Connection state logging
            @self._pc.on("connectionstatechange")
            async def on_state():
                state = self._pc.connectionState
                self._log(f"[WebRTC] Connection state: {state}")
                if state == "connected":
                    self._log("[WebRTC] P2P CONNECTED — video streaming")
                elif state in ("failed", "closed", "disconnected"):
                    self._log(f"[WebRTC] Connection {state}")

            # ── DataChannel: terima input mouse/keyboard dari browser ──
            @self._pc.on("datachannel")
            def on_datachannel(channel):
                self._data_channel = channel
                self._log(f"[WebRTC] DataChannel '{channel.label}' opened")

                @channel.on("message")
                def on_message(message):
                    try:
                        import json as _json
                        event = _json.loads(message)
                        etype = event.get("type", "unknown")

                        if etype != "mouse_move":
                            self._log(f"[WebRTC Input] {etype}")

                        # Ambil ukuran layar aktual
                        try:
                            import win32api as _w32
                            sw = _w32.GetSystemMetrics(0)
                            sh = _w32.GetSystemMetrics(1)
                            self._screen_w = sw
                            self._screen_h = sh
                        except Exception:
                            sw, sh = self._screen_w, self._screen_h

                        # Eksekusi via RemoteControlAgent
                        if self._remote_ctrl:
                            self._remote_ctrl._execute_event(event, sw, sh)
                        else:
                            self._log("[WebRTC Input] WARNING: remote_ctrl not set")
                    except Exception as _e:
                        self._log(f"[WebRTC Input] msg error: {_e}")

                @channel.on("close")
                def on_close():
                    self._log("[WebRTC] DataChannel closed")
                    self._data_channel = None

            # Set remote description (browser offer)
            offer = RTCSessionDescription(sdp=offer_sdp, type=offer_type)
            await self._pc.setRemoteDescription(offer)

            # Buat answer
            answer = await self._pc.createAnswer()
            await self._pc.setLocalDescription(answer)

            # Fix SDP untuk Chrome
            raw_sdp = self._pc.localDescription.sdp
            sdp = fix_sdp_for_chrome(raw_sdp)

            self._log(f"[WebRTC] Answer created ({len(sdp.split(chr(13)))} lines)")

            # Kirim answer via server → browser
            self._sender.send_webrtc_answer(sdp=sdp, sdp_type=self._pc.localDescription.type)
            self._log("[WebRTC] Answer sent to admin")

        except Exception as e:
            self._log(f"[WebRTC] _create_answer ERROR: {e}")
            import traceback
            self._log(traceback.format_exc())

    async def _set_remote_answer(self, sdp: str, sdp_type: str):
        """Mode legacy: agent offerer, terima answer dari browser."""
        if not self._pc:
            return
        answer = RTCSessionDescription(sdp=sdp, type=sdp_type)
        await self._pc.setRemoteDescription(answer)
        self._log("[WebRTC] Remote description set")

    async def _add_ice_candidate(self, candidate: dict):
        """Tambah ICE candidate dari browser."""
        if not self._pc or not candidate:
            return
        try:
            from aiortc.sdp import candidate_from_sdp
            cand_str = candidate.get("candidate", "")
            c = candidate_from_sdp(cand_str)
            c.sdpMid = candidate.get("sdpMid")
            c.sdpMLineIndex = candidate.get("sdpMLineIndex")
            await self._pc.addIceCandidate(c)
        except Exception as e:
            self._log(f"[WebRTC] ICE candidate error: {e}")

    async def _close(self):
        """Tutup peer connection."""
        if self._pc:
            try:
                await self._pc.close()
            except Exception:
                pass
            self._pc = None
            self._data_channel = None
            self._log("[WebRTC] Connection closed")