"""
webrtc_streamer.py - WebRTC screen capture streamer untuk agent monitoring.
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
    logger.warning("[WebRTC] aiortc tidak tersedia. Jalankan: pip install aiortc aiohttp")


class ScreenCaptureTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self, fps: int = 15):
        super().__init__()
        self._fps      = fps
        self._interval = 1.0 / fps
        self._last_t   = 0.0
        self._sct      = None

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        now = time.monotonic()
        sleep = self._interval - (now - self._last_t)
        if sleep > 0:
            await asyncio.sleep(sleep)
        self._last_t = time.monotonic()
        if self._sct is None:
            self._sct = mss.mss()
        monitor = self._sct.monitors[1]
        shot    = self._sct.grab(monitor)
        img = np.frombuffer(shot.raw, dtype=np.uint8)
        img = img.reshape((shot.height, shot.width, 4))
        img = img[:, :, :3][:, :, ::-1].copy()
        frame           = av.VideoFrame.from_ndarray(img, format="rgb24")
        frame.pts       = pts
        frame.time_base = time_base
        return frame


def fix_sdp_for_chrome(raw_sdp: str) -> str:
    """
    Reorder SDP agar kompatibel dengan Chrome:
    - ice-ufrag, ice-pwd, fingerprint, setup harus SEBELUM candidates
    - Hanya simpan fingerprint sha-256 (hapus sha-384 & sha-512)
    - Line endings CRLF
    """
    lines = raw_sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # Pisah session dan media sections
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
        # Skip fingerprint selain sha-256, dan skip empty lines
        others = [l for l in ml
                  if l not in skip
                  and not l.startswith("a=fingerprint:")
                  and l.strip() != ""]
        return m + c + dtls + others + cands + eoc

    result = session
    for ms in media_sections:
        result = result + reorder(ms)

    return "\r\n".join(result) + "\r\n"  # SDP harus diakhiri CRLF


class WebRtcStreamer:
    def __init__(self, data_sender, log_callback=None, fps: int = 15):
        self._sender  = data_sender
        self._log     = log_callback or (lambda msg: None)
        self._fps     = fps
        self._pc      = None
        self._loop    = None
        self._thread  = None
        self._running = False

    def is_available(self) -> bool:
        return AIORTC_AVAILABLE

    def start(self):
        if self._running:
            return
        if not AIORTC_AVAILABLE:
            self._log("[WebRTC] aiortc tidak tersedia")
            return
        self._running = True
        self._loop    = asyncio.new_event_loop()
        self._thread  = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._log("[WebRTC] Event loop started")

    def on_request(self):
        if not self._loop:
            self.start()
        self._log("[WebRTC] Received request, creating offer...")
        asyncio.run_coroutine_threadsafe(self._create_offer(), self._loop)

    def on_browser_offer(self, sdp: str, sdp_type: str):
        if not self._loop:
            self.start()
        self._log("[WebRTC] Browser offer received, creating answer...")
        asyncio.run_coroutine_threadsafe(self._create_answer(sdp, sdp_type), self._loop)

    def on_answer(self, sdp: str, sdp_type: str):
        if not self._loop or not self._pc:
            return
        asyncio.run_coroutine_threadsafe(self._set_remote_answer(sdp, sdp_type), self._loop)

    def on_ice_candidate(self, candidate: dict):
        if not self._loop or not self._pc:
            return
        asyncio.run_coroutine_threadsafe(self._add_ice_candidate(candidate), self._loop)

    def on_stop(self):
        if self._loop and self._pc:
            asyncio.run_coroutine_threadsafe(self._close(), self._loop)

    def stop(self):
        self._running = False
        if self._loop and self._pc:
            asyncio.run_coroutine_threadsafe(self._close(), self._loop)

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _create_offer(self):
        if self._pc:
            await self._pc.close()
        self._pc  = RTCPeerConnection()
        track     = ScreenCaptureTrack(fps=self._fps)
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
        try:
            if self._pc:
                await self._pc.close()

            self._pc  = RTCPeerConnection()
            track     = ScreenCaptureTrack(fps=self._fps)
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

            # Set browser offer
            offer = RTCSessionDescription(sdp=offer_sdp, type=offer_type)
            await self._pc.setRemoteDescription(offer)

            # Buat answer
            answer = await self._pc.createAnswer()
            await self._pc.setLocalDescription(answer)

            # Fix SDP untuk Chrome
            raw_sdp = self._pc.localDescription.sdp
            sdp     = fix_sdp_for_chrome(raw_sdp)

            # Log untuk debug
            sdp_lines   = sdp.split("\r\n")
            fp_lines    = [l for l in sdp_lines if l.startswith("a=fingerprint:")]
            setup_lines = [l for l in sdp_lines if l.startswith("a=setup:")]
            self._log(f"[WebRTC] Answer: {len(sdp_lines)} lines, fp={fp_lines}, setup={setup_lines}")

            # Force 127.0.0.1 di ICE candidates agar tidak perlu firewall
            sdp_lines_lb = []
            for ln in sdp.split("\r\n"):
                if ln.startswith("a=candidate:"):
                    parts = ln.split()
                    # Skip srflx - hanya keep host candidate (IP asli dari aiortc)
                    if len(parts) >= 8 and "typ" in parts:
                        typ_idx = parts.index("typ")
                        if parts[typ_idx + 1] == "srflx":
                            continue  # Skip srflx candidate
                    # Gunakan IP asli aiortc (tidak diubah ke 127.0.0.1)
                sdp_lines_lb.append(ln)
            sdp_lb = "\r\n".join(sdp_lines_lb)
            self._log("[WebRTC] Candidates: " + str([l for l in sdp_lines_lb if "candidate:" in l]))
            self._log("[WebRTC] Answer created, sending to admin...")
            self._sender.send_webrtc_answer(sdp=sdp_lb, sdp_type=self._pc.localDescription.type)

        except Exception as e:
            self._log(f"[WebRTC] _create_answer ERROR: {e}")
            import traceback
            self._log(traceback.format_exc())

    async def _set_remote_answer(self, sdp: str, sdp_type: str):
        if not self._pc:
            return
        answer = RTCSessionDescription(sdp=sdp, type=sdp_type)
        await self._pc.setRemoteDescription(answer)
        self._log("[WebRTC] Remote description set")

    async def _add_ice_candidate(self, candidate: dict):
        if not self._pc or not candidate:
            self._log(f"[WebRTC] addIceCandidate skipped: pc={self._pc is not None}, cand={bool(candidate)}")
            return
        try:
            from aiortc.sdp import candidate_from_sdp
            cand_str = candidate.get("candidate", "")
            self._log(f"[WebRTC] Adding ICE candidate: {cand_str[:60]}")
            c = candidate_from_sdp(cand_str)
            c.sdpMid        = candidate.get("sdpMid")
            c.sdpMLineIndex = candidate.get("sdpMLineIndex")
            await self._pc.addIceCandidate(c)
            self._log(f"[WebRTC] ICE candidate added OK")
        except Exception as e:
            self._log(f"[WebRTC] ICE candidate error: {e}")

    async def _close(self):
        if self._pc:
            await self._pc.close()
            self._pc = None
            self._log("[WebRTC] Connection closed")
