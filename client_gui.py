from ctypes import c_wchar
from fractions import Fraction


import customtkinter as ctk
from customtkinter import CTkImage
import websockets
from PIL import Image, ImageTk
from aiortc.sdp import candidate_from_sdp
import cv2
import av
from av import VideoFrame
from aiortc.mediastreams import MediaStreamTrack
import json
import io
import time
import threading
import sys
import ssl
import traceback
import tkinter



from crypto_e2ee import pubkey_from_bytes, derive_aes_key

import winsound
from aiortc import RTCConfiguration, RTCIceServer
import pydub # new
import os    # new
import base64 # new
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack
from av import AudioFrame
import asyncio   #
import sounddevice as sd  #
import numpy as np

import sys
import os

def resource_path(relative_path):
    """ .exe olarak çalışırken kaynak dosyalarına doğru yolu alır """
    try:
        # PyInstaller geçici bir klasör oluşturur ve yolu _MEIPASS içinde saklar
        base_path = sys._MEIPASS

        # ---- YENİ SATIR ----
        # PyInstaller'ın 'data' dosyalarını (ffmpeg vb.) koyduğu
        # _internal klasörünü de yola ekle.
        base_path = os.path.join(base_path, ".")
        # ---- YENİ SATIR SONU ----

    except Exception:
        # .exe olarak çalışmıyorsa (normal .py ise)
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

if sys.stdin is None:
    sys.stdin = io.StringIO()
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# --- new block--- to show pydub ffmpeg's place
try:

    ffmpeg_path = resource_path("ffmpeg.exe")
    ffprobe_path = resource_path("ffprobe.exe")

    # hardcore these paths to pydub lib
    pydub.AudioSegment.converter = ffmpeg_path
    pydub.AudioSegment.ffprobe = ffprobe_path

    print("DEBUG: ffmpeg motoru pydub'a başarıyla bağlandı.")
except Exception as e:
    print(f"UYARI: ffmpeg/ffprobe yüklenemedi. Ses sıkıştırma çalışmayabilir. Hata: {e}")





ctk.set_appearance_mode("dark")

ctk.set_default_color_theme("blue")



#sound catcher class

class SoundDeviceAudioTrack(MediaStreamTrack):
    """
    Geliştirilmiş, hataya dayanıklı mikrofon sınıfı.
    aiortc'nin 'stop' çağırmasını engellemek için sessizlik koruması içerir.
    """
    kind = "audio"

    def __init__(self, loop, samplerate=48000, channels=1):
        super().__init__()
        self.loop = loop

        # --- AYAR 1: WebRTC/Opus Standartları ---
        # Opus kodeği 48kHz ve 20ms frame (960 sample) ile en iyi çalışır.
        # Bu ayarlar veri uyuşmazlığı hatalarını önler.
        self.samplerate = 48000
        self.channels = 1  # Mono ses (WebRTC için en güvenlisi)
        self.dtype = 'int16'
        self.blocksize = 960  # 48000Hz / 50fps = 960 sample (20ms)

        self.stream = None
        self.thread = None
        self.queue = asyncio.Queue()
        self._running = True
        self.started_event = asyncio.Event()
        self.timestamp = 0  # Zaman damgasını başlat

    def start_stream(self):
        def audio_callback(indata, frames, time, status):
            if status:
                print(f"UYARI (Mic): Ses durumu: {status}")


            if not self._running:
                raise sd.CallbackStop

            # Stereo gelirse Mono'ya çevir
            if indata.shape[1] > 1:
                indata = np.mean(indata, axis=1, keepdims=True).astype(self.dtype)

            if self.queue.qsize() > 50:
                try:
                    self.queue.get_nowait()  # En eskiyi çöpe at
                except asyncio.QueueEmpty:
                    pass
            # Veriyi kuyruğa at
            self.loop.call_soon_threadsafe(self.queue.put_nowait, indata.copy())

        try:
            self.stream = sd.InputStream(
                samplerate=self.samplerate,
                blocksize=self.blocksize,
                dtype=self.dtype,
                channels=self.channels,
                callback=audio_callback,

            )
            self.stream.start()
            self.loop.call_soon_threadsafe(self.started_event.set)
            print("DEBUG (MicTrack): Mikrofon donanımı başlatıldı (48kHz/Mono).")
        except Exception as e:
            print(f"HATA (MicTrack): InputStream başlatılamadı: {e}")
            self._running = False
            self.loop.call_soon_threadsafe(self.started_event.set)

    async def recv(self):
        """aiortc tarafından sürekli çağrılan kritik döngü."""
        if not self._running:
            raise asyncio.CancelledError

        try:
            # Kuyruktan veri al
            indata = await self.queue.get()
            indata = indata.T

            # Frame oluştur
            frame = AudioFrame.from_ndarray(indata, format='s16', layout='mono')
            frame.sample_rate = self.samplerate

            # Zaman damgalarını (PTS) doğru ayarla
            frame.pts = self.timestamp
            frame.time_base = Fraction(1, self.samplerate)

            # Bir sonraki paket için damgayı ilerlet
            self.timestamp += self.blocksize

            return frame

        except Exception as e:
            print(f"KRİTİK HATA (MicTrack.recv): {e}")
            import traceback
            traceback.print_exc()

            # --- AYAR 2: Sessizlik Koruması (Silence Fallback) ---
            # Eğer veri okurken hata olursa, aiortc'nin "stop" demesini engellemek için
            # boş (sessiz) bir frame gönderiyoruz.
            print("DEBUG (MicTrack): Hata kurtarılıyor, SESSİZLİK gönderiliyor...")

            silence_data = np.zeros((self.blocksize, 1), dtype=self.dtype)
            frame = AudioFrame.from_ndarray(silence_data, format='s16', layout='mono')
            frame.sample_rate = self.samplerate
            frame.pts = self.timestamp
            frame.time_base = Fraction(1, self.samplerate)
            self.timestamp += self.blocksize

            return frame

    async def start(self):
        if self.thread is None:
            self._running = True
            self.started_event.clear()
            self.thread = threading.Thread(target=self.start_stream, daemon=True)
            self.thread.start()
            await self.started_event.wait()

    def stop(self):
        if self._running:
            # print("DEBUG (MicTrack): Durduruluyor...") # Debug kirliliğini azaltmak için kapattım
            self._running = False
            if self.thread:
                self.thread.join(timeout=1)
                self.thread = None
            if self.stream:
                self.stream.stop()
                self.stream.close()
                self.stream = None

class DummyVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, loop, color=(0, 255, 0)):
        super().__init__()
        self.loop = loop
        self.color = color  # RGB renk (default yeşil)
        self._running = True

    async def recv(self):
        # 320x240 sabit renkli kare üret
        img = np.zeros((240, 320, 3), dtype=np.uint8)
        img[:] = self.color
        frame = VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = 0
        frame.time_base = Fraction(1,30)  # 30 FPS
        return frame

    def stop(self):
        self._running = False


class CameraVideoTrack(MediaStreamTrack):
    """
    Doğru zaman damgası (PTS) ve thread-safe kamera okuması
    kullanan düzeltilmiş video track sınıfı.
    """
    kind = "video"

    def __init__(self, loop, camera_index=0):
        super().__init__()
        self.loop = loop
        self.camera_index = camera_index
        self.cap = None
        self._running = True
        self.queue = asyncio.Queue()

        # --- YENİ ZAMAN DAMGASI DEĞİŞKENLERİ (Ses kodundan  esinlenildi) ---
        self.timestamp = 0
        self.fps = 30  # Saniyedeki Kare Sayısı (Hedef)
        self.time_base = Fraction(1, self.fps)  # Zaman tabanı (1/30 saniye)
        self.sleep_time = 1 / self.fps  # 30 FPS için kareler arası bekleme süresi
        # --- YENİ DEĞİŞKENLER SONU ---

        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()
        print("DEBUG (CameraVideoTrack): __init__ tamamlandı, _reader thread'i başlatıldı.")

    def _reader(self):
        print(f"DEBUG (CameraVideoTrack): _reader thread'i kamerayı ({self.camera_index}) açmayı deniyor...")
        self.cap = cv2.VideoCapture(self.camera_index + cv2.CAP_DSHOW)

        if not self.cap.isOpened():
            print(f"HATA (CameraVideoTrack): _reader thread'i kamerayı AÇAMADI.")
            self._running = False
        else:
            print("DEBUG (CameraVideoTrack): _reader thread'i kamerayı AÇTI. Döngü başlıyor.")

        frame_time_start = time.time()

        while self._running:
            try:
                if not self.cap or not self.cap.isOpened():
                    self._running = False
                    break

                ret, frame = self.cap.read()
                if not ret:
                    print("UYARI (CameraVideoTrack): cap.read() False döndürdü.")
                    time.sleep(0.1)
                    continue

                # --- 1. KRİTİK DEĞİŞİKLİK (Çalışan koddaki gibi) ---
                # Kareyi BGR'den RGB'ye manuel olarak çevir
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                frame_copy = frame.copy()  # Uyumluluk için kareyi kopyala
                video_frame = VideoFrame.from_ndarray(frame_copy, format="bgr24")  #

                # --- DÜZELTİLMİŞ ZAMAN DAMGASI MANTIĞI ---
                pts = int(time.time() * 90000)  # 90kHz saat standardı
                video_frame.pts = pts
                video_frame.time_base = Fraction(1, 90000)
                # --- DÜZELTME SONU ---

                # Kareyi asyncio kuyruğuna güvenle gönder
                self.loop.call_soon_threadsafe(self.queue.put_nowait, video_frame)  # [cite: 158]

                # --- YENİ FPS LİMİTLEYİCİ ---
                # Thread'in işlemciyi kilitlememesi ve 30 FPS'e uyması için uyu
                frame_time_end = time.time()
                elapsed = frame_time_end - frame_time_start
                sleep_duration = self.sleep_time - elapsed
                if sleep_duration > 0:
                    time.sleep(sleep_duration)
                frame_time_start = time.time()  # Bir sonraki döngü için zamanı sıfırla
                # --- YENİ LİMİTLEYİCİ SONU ---

            except Exception as e:
                print(f"HATA (CameraVideoTrack._reader): Thread çöktü: {e}")
                import traceback
                traceback.print_exc(file=sys.stderr)
                self._running = False
                break

        if self.cap:
            self.cap.release()  # [cite: 162]
            print("DEBUG (CameraVideoTrack): _reader thread'i sonlandı ve kamerayı serbest bıraktı.")

    async def recv(self):
        return await self.queue.get()  # [cite: 161]

    def stop(self):
        print("DEBUG (CameraVideoTrack): stop() çağrıldı.")
        self._running = False
        if hasattr(self, 'thread') and self.thread:
            self.thread.join(timeout=1)
        self.cap = None


class WebRTCManager:
    def __init__(self, master_app, target_username):
        self.camera_track = None
        self.master_app = master_app
        self.target_username = target_username
        self.loop = master_app.asyncio_loop
        self.speaker_stream = None
        self.speaker_task = None
        self.mic_track = SoundDeviceAudioTrack(master_app.asyncio_loop)

        # PC'yi burada başlatmıyoruz, 'None' yapıyoruz.
        self.pc = None

    async def _ensure_pc(self):
        """RTCPeerConnection'ı doğru thread içinde ve güvenli ayarlarla başlatır."""
        if self.pc:
            return  # Zaten varsa dokunma

        print(f"DEBUG ({self.target_username}): RTCPeerConnection (PC) başlatılıyor...")

        # --- AYAR 1: Localhost için STUN sunucusunu BOŞ bırakıyoruz ---
        # Bu, yerel ağda bağlantının anında kurulmasını sağlar.
        config = RTCConfiguration(iceServers=[])
        self.pc = RTCPeerConnection(configuration=config)

        @self.pc.on("connectionstatechange")
        async def on_connectionstatechange():
            print(f"WebRTC Durumu ({self.target_username}): {self.pc.connectionState}")

            window = self.master_app.private_chat_windows.get(self.target_username)
            if not window:
                return

            new_status_text = ""
            if self.pc.connectionState == "connected":
                new_status_text = "📞 Bağlandı (Ses Aktif)"

            elif self.pc.connectionState == "failed":
                # --- DÜZELTME: "end_call" KOMUTU SİLİNDİ ---
                new_status_text = "⚠️ Bağlantı Zayıf (Kapatılmıyor, bekleniyor...)"
                print("UYARI: Bağlantı durumu 'failed' oldu ama mikrofon açık tutuluyor.")

            elif self.pc.connectionState == "disconnected":
                new_status_text = "⚠️ Bağlantı Kesildi (Bekleniyor...)"

            elif self.pc.connectionState == "closed":
                new_status_text = "Arama Kapatıldı."

            # Label güncelleme (Lambda ile hatasız)
            if new_status_text:
                self.master_app.schedule_gui_update(
                    lambda: window.call_status_label.configure(text=new_status_text)
                )

        @self.pc.on("track")
        def on_track(track):
            print(f"DEBUG ({self.target_username}): Track alındı: {track.kind}")
            if track.kind == "audio":
                try:
                    # Hoparlörü güvenli başlat
                    self.speaker_stream = sd.OutputStream(
                        samplerate=48000, channels=1, dtype='int16', blocksize=960, latency=0.1
                    )
                    self.speaker_stream.start()
                    self.speaker_task = asyncio.ensure_future(self.run_speaker(track))
                    print(f"DEBUG ({self.target_username}): Hoparlör aktif.")
                except Exception as e:
                    print(f"UYARI: Hoparlör başlatılamadı (Mikrofon devam ediyor): {e}")

            elif track.kind == "video":
                window = self.master_app.private_chat_windows.get(self.target_username)
                if window:
                    asyncio.ensure_future(window.run_video(track))

        @self.pc.on("icecandidate")
        def on_icecandidate(event):
            if event.candidate:
                # Aday bulunduğunda sunucuya gönder
                print(f"DEBUG ({self.target_username}): ICE Adayı bulundu -> {event.candidate.type}")
                self.send_signal("CALL_CANDIDATE", event.candidate.to_sdp())

    async def add_camera_track(self, use_dummy=False):
        await self._ensure_pc()
        if not hasattr(self, "camera_track") or self.camera_track is None:
                self.camera_track = CameraVideoTrack(self.loop)
                print(f"DEBUG ({self.target_username}): Gerçek kamera track eklendi.")
                self.pc.addTrack(self.camera_track)
        else:
            print(f"DEBUG ({self.target_username}): Kamera track zaten mevcut.")

    async def renegotiate(self):
        print(f"DEBUG ({self.target_username}): Renegotiation başlatılıyor...")
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        print(f"DEBUG ({self.target_username}): LocalDescription offer ayarlandı.")
        self.send_signal("CALL_OFFER", offer.sdp)

    async def remove_camera_track(self):
        if hasattr(self, "camera_track") and self.camera_track:
            self.camera_track.stop()
            senders = [s for s in self.pc.getSenders() if s.track == self.camera_track]
            for sender in senders:
                try:
                    await sender.replaceTrack(None) # <-- DÜZELTME
                except Exception as e:
                    print(f"HATA (removeTrack): {e}")
            self.camera_track = None
            print(f"DEBUG ({self.target_username}): Kamera track kaldırıldı.")

    async def run_speaker(self, track):
        """Gelen ses verisini hoparlöre yazar (Kritik Düzeltilmiş Versiyon)."""
        print(f"DEBUG ({track.kind}): Hoparlör görevi başlatıldı.")

        import av
        # Gelen sesi 48000Hz Mono formatına çeviren ayarlayıcı
        resampler = av.AudioResampler(format='s16', layout='mono', rate=48000)

        # Hoparlör stream'i yoksa oluştur
        if self.speaker_stream is None or not self.speaker_stream.active:
            # Sizin kodunuzdaki speaker_stream oluşturma mantığına uygun olmalı.
            # (Varsayım: Sounddevice stream'i 48000Hz, 1 kanal, 16 bit bekliyor)
            try:
                import sounddevice as sd
                self.speaker_stream = sd.OutputStream(
                    samplerate=48000,
                    channels=1,
                    dtype='int16'
                )
                self.speaker_stream.start()
            except Exception as e:
                print(f"HATA: Hoparlör stream'i başlatılamadı: {e}")
                return  # Başlatamazsa görevi sonlandır

        try:
            while True:
                # 1. Ağdan ses paketini bekle
                frame = await track.recv()

                # 2. Sesi yeniden örnekle
                resampled_frames = resampler.resample(frame)

                for r_frame in resampled_frames:
                    # PyAV'dan numpy dizisine çevir (Sounddevice için zorunlu)
                    data = r_frame.to_ndarray()

                    # Şekil Düzeltmesi (Emin olmak için)
                    if data.ndim == 2 and data.shape[0] < data.shape[1]:
                        data = data.T

                    # Bellek Hizalaması (C contiguous)
                    if not data.flags['C_CONTIGUOUS']:
                        data = np.ascontiguousarray(data)

                    # 3. Hoparlöre Yaz (Asenkron ortamda engellemeyen yazma)
                    if self.speaker_stream and self.speaker_stream.active:
                        await self.loop.run_in_executor(None, self.speaker_stream.write, data)

        except asyncio.CancelledError:
            print(f"DEBUG ({track.kind}): Hoparlör iptal edildi.")
        except Exception as e:
            print(f"HATA (run_speaker): {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Görev bittiğinde veya hata verdiğinde temizle
            if self.speaker_stream:
                self.speaker_stream.stop()
                self.speaker_stream.close()
                self.speaker_stream = None
    async def blocking_write(data):
        try:
            self.speaker_stream.write(data)
        except Exception as e:
                print(f"HATA (blocking_write): {e}")

        try:
            while True:
                frame = await track.recv()
                arr = frame.to_ndarray(format='s16')  # ndarray
                if self.speaker_stream.channels == 2 and arr.ndim == 1:
                    arr = np.repeat(arr[:, np.newaxis], 2, axis=1)
                if self.speaker_stream.channels == 1 and arr.ndim == 2:
                    arr = np.mean(arr, axis=1).astype(np.int16)


        except asyncio.CancelledError:
            print(f"DEBUG ({self.target_username}): Hoparlör görevi durduruldu.")
        except Exception as e:
            print(f"Hoparlör akış hatası: {e}")

    async def add_mic_track(self):
        # 1. PC'nin varlığından emin ol (Hata koruması)
        if self.pc is None:
            print(f"UYARI: add_mic_track çağrıldı ama PC yok. Oluşturuluyor...")
            await self._ensure_pc()

        # 2. Mikrofon nesnesi yoksa oluştur
        if self.mic_track is None:
            print(f"DEBUG ({self.target_username}): Mikrofon nesnesi oluşturuluyor...")
            self.mic_track = SoundDeviceAudioTrack(self.loop)

        # 3. Zaten ekli mi kontrol et
        senders = [s for s in self.pc.getSenders() if s.track == self.mic_track]
        if senders:
            print(f"DEBUG: Mikrofon zaten ekli.")
            if not self.mic_track._running:
                await self.mic_track.start()
            return

        # 4. Ekle ve Başlat
        print(f"DEBUG ({self.target_username}): Mikrofon PC'ye ekleniyor...")
        self.pc.addTrack(self.mic_track) # Artık self.pc'nin None olma şansı yok
        await self.mic_track.start()

    def send_signal(self, command, sdp_or_candidate):
        self.master_app.send_call_signal(command, self.target_username, {"sdp": sdp_or_candidate})

    async def create_offer(self):
        print(f"DEBUG ({self.target_username}): 1. PC Hazırlanıyor...")
        await self._ensure_pc()

        print(f"DEBUG ({self.target_username}): 2. Mikrofon Ekleniyor...")
        await self.add_mic_track()

        print(f"DEBUG ({self.target_username}): 3. Offer Oluşturuluyor...")
        offer = await self.pc.createOffer()

        # Bu işlem, arka planda ICE Gathering (IP toplama) sürecini başlatır
        await self.pc.setLocalDescription(offer)

        # --- KRİTİK DÜZELTME ---
        # Paylaştığınız çözümdeki gibi, ICE toplama işleminin bitmesini bekliyoruz.
        # aiortc kütüphanesinde bunu yapmanın en pratik yolu kısa bir beklemedir.
        print("DEBUG: Ağ adayları (IPler) toplanıyor, lütfen bekleyin...")

        # IP'lerin bulunması için 2 saniye bekle (Localhost için 1sn de yeter ama 2 garanti olsun)
        await asyncio.sleep(2)
        # -----------------------

        # HATA BURADAYDI: Eskiden 'offer.sdp' gönderiyordunuz, o ilk haliydi ve boştu.
        # Şimdi 'self.pc.localDescription.sdp' gönderiyoruz, çünkü bu geçen 2 saniyede güncellendi.
        final_sdp = self.pc.localDescription.sdp
        print(f"DEBUG: Tamamlanmış Offer gönderiliyor (SDP Boyutu: {len(final_sdp)})")

        self.send_signal("CALL_OFFER", final_sdp)

    async def handle_offer(self, offer_sdp):
        print(f"DEBUG ({self.target_username}): 1. PC Hazırlanıyor...")
        await self._ensure_pc()

        # Gelen teklifi işle
        offer_desc = RTCSessionDescription(sdp=offer_sdp, type="offer")
        await self.pc.setRemoteDescription(offer_desc)

        print(f"DEBUG ({self.target_username}): 2. Mikrofon Ekleniyor...")
        await self.add_mic_track()

        print(f"DEBUG ({self.target_username}): 3. Answer Oluşturuluyor...")
        answer = await self.pc.createAnswer()

        # Bu işlem ICE Gathering sürecini tetikler
        await self.pc.setLocalDescription(answer)

        # --- KRİTİK DÜZELTME ---
        # Cevap veren tarafın da kendi IP'lerini bulması için bekle
        print("DEBUG: Ağ adayları (IPler) toplanıyor, lütfen bekleyin...")
        await asyncio.sleep(2)
        # -----------------------

        # Güncellenmiş ve IP adreslerini içeren nihai cevabı gönder
        final_sdp = self.pc.localDescription.sdp
        print(f"DEBUG: Tamamlanmış Answer gönderiliyor (SDP Boyutu: {len(final_sdp)})")

        self.send_signal("CALL_ANSWER", final_sdp)

    async def handle_answer(self, answer_sdp):
        answer_desc = RTCSessionDescription(sdp=answer_sdp, type="answer")
        await self.pc.setRemoteDescription(answer_desc)
        print(f"DEBUG ({self.target_username}): P2P el sıkışma tamamlandı.")

    async def add_ice_candidate_sdp(self, candidate_sdp: str):
        await self._ensure_pc()
        if not self.pc.remoteDescription:
            print(f"UYARI ({self.target_username}): RemoteDescription yok, ICE adayı bekletiliyor/atlandı.")
            # İdeal dünyada bunu bir kuyruğa atıp sonra tekrar denemeliyiz,
            # ama şimdilik hatayı görmek için log ekledik.
            return

        try:
            cand = candidate_from_sdp(candidate_sdp)
            await self.pc.addIceCandidate(cand)
            print(f"DEBUG ({self.target_username}): ICE Adayı başarıyla eklendi.")
        except Exception as e:
            print(f"HATA (ICE): Aday eklenemedi: {e}")

    async def stop_media(self):
        if self.speaker_task:
            self.speaker_task.cancel()
            self.speaker_task = None
        if self.speaker_stream:
            self.speaker_stream.stop()
            self.speaker_stream.close()
            self.speaker_stream = None
        if self.camera_track:
            self.camera_track.stop()
            self.camera_track = None

        if self.mic_track:
            self.mic_track.stop()
            self.mic_track = None

    async def close(self):
        await self.stop_media()
        await self.pc.close()


class PrivateChatWindow(ctk.CTkToplevel):
    """
    Belirli bir kullanıcıyla yapılan özel sohbet için
    açılır pencere sınıfı.
    """

    # PrivateChatWindow sınıfı içinde
    def __init__(self, master, target_username):
        super().__init__(master)
        self.master_app = master
        self.target_username = target_username
        self.rtc_manager = WebRTCManager(self.master_app, self.target_username)

        self.title(f"💬 {self.target_username}")
        self.geometry("400x600")
        self.configure(fg_color="#1a1a1a")  # Tüm pencere arkaplanı (Çok koyu)

        self.video_enabled = False
        self.video_state = "idle"
        self._video_dialog_buttons = None

        # --- Grid Yapılandırması ---
        self.grid_rowconfigure(0, weight=0)  # Header (Sabit)
        self.grid_rowconfigure(1, weight=0)  # Video Alanı (Gerektiğinde genişler)
        self.grid_rowconfigure(2, weight=1)  # Sohbet (Esnek)
        self.grid_rowconfigure(3, weight=0)  # Input (Sabit)
        self.grid_columnconfigure(0, weight=1)

        # --- 1. HEADER (Üst Panel) ---
        self.header_frame = ctk.CTkFrame(self, fg_color="#212121", corner_radius=0, height=60)
        self.header_frame.grid(row=0, column=0, sticky="ew")

        # Profil İkonu ve İsim
        self.avatar_lbl = ctk.CTkLabel(self.header_frame, text="👤", font=("Arial", 24))
        self.avatar_lbl.pack(side="left", padx=(15, 5), pady=10)

        self.name_lbl = ctk.CTkLabel(self.header_frame, text=self.target_username,
                                     font=("Roboto", 16, "bold"), text_color="white")
        self.name_lbl.pack(side="left", padx=5)

        # Arama Butonları (Header'ın sağına)
        self.call_button = ctk.CTkButton(self.header_frame, text="📞", width=40, height=35,
                                         fg_color="#27ae60", hover_color="#2ecc71",
                                         command=self.initiate_call)
        self.call_button.pack(side="right", padx=10)

        self.video_button = ctk.CTkButton(self.header_frame, text="📹", width=40, height=35,
                                          fg_color="#8e44ad", hover_color="#9b59b6",
                                          command=self.toggle_video)
        self.video_button.pack(side="right", padx=(0, 5))

        # Arama Durum Metni (İsim altına veya yanına sıkıştırmak yerine header altına gizli bar olabilir
        # ama şimdilik header içinde ismin yanına küçük yazalım)
        self.call_status_label = ctk.CTkLabel(self.header_frame, text="", font=("Arial", 10), text_color="#aaaaaa")
        self.call_status_label.pack(side="right", padx=10)

        # Bitir Butonu (Başlangıçta gizli)
        self.end_call_button = ctk.CTkButton(self.header_frame, text="❌ Bitir", width=60, height=35,
                                             fg_color="#c0392b", hover_color="#e74c3c",
                                             command=self.end_call)

        # --- 2. VIDEO ALANI (Header ile Chat Arasında) ---
        self.video_frame = ctk.CTkFrame(self, fg_color="black", height=0)
        # (Başlangıçta grid yapmıyoruz, video açılınca gridleyeceğiz)

        self.video_label = ctk.CTkLabel(self.video_frame, text="", fg_color="transparent")
        self.video_label.pack(fill="both", expand=True, padx=2, pady=2)

        # --- 3. SOHBET ALANI (Scrollable) ---
        # Eski Textbox yerine ScrollableFrame kullanıyoruz (Baloncuklar için)
        self.chat_box = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.chat_box.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)
        self.chat_box.grid_columnconfigure(0, weight=1)

        # --- 4. INPUT ALANI (Alt Kısım) ---
        self.input_bg = ctk.CTkFrame(self, fg_color="#212121", corner_radius=0)
        self.input_bg.grid(row=3, column=0, sticky="ew")
        self.input_bg.grid_columnconfigure(0, weight=1)

        # Kapsül Container
        self.input_container = ctk.CTkFrame(self.input_bg, fg_color="#383838", corner_radius=20)
        self.input_container.grid(row=0, column=0, sticky="ew", padx=15, pady=15)
        self.input_container.grid_columnconfigure(0, weight=1)

        self.message_entry = ctk.CTkEntry(self.input_container, placeholder_text="Mesaj yaz...",
                                          border_width=0, fg_color="transparent", height=40)
        self.message_entry.grid(row=0, column=0, sticky="ew", padx=10)
        self.message_entry.bind("<Return>", self.send_message_event)

        self.send_button = ctk.CTkButton(self.input_container, text="➤", width=40, height=35,
                                         fg_color="#3B8ED0", hover_color="#2678B8", corner_radius=15,
                                         command=self.send_message_event)
        self.send_button.grid(row=0, column=1, padx=(0, 5))

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def initiate_call(self):
            """'Ara' butonuna basıldığında."""
            self.call_status_label.configure(text=f"{self.target_username} aranıyor...")
            self.call_button.pack_forget()  # Ara butonunu gizle
            self.end_call_button.pack(side="right", padx=5)  # Bitir butonunu göster

            # Ana uygulamaya (ChatApp) sunucuya göndermesi için sinyal ver
            self.master_app.send_call_signal("CALL_REQUEST", self.target_username)

    def set_call_ui_to_active(self, status_text="Arama bağlandı! (P2P kuruluyor...)"):
        """Arayüzü 'arama-içi' durumuna geçirir (Butonları günceller)."""
        self.call_status_label.configure(text=status_text)
        self.call_button.pack_forget()  # Ara butonunu gizle
        self.end_call_button.pack(side="right", padx=5)  # Bitir butonunu göster

    def end_call(self, notify_server=True):
        # Video alanını gizle
            self.video_frame.grid_forget()
            """'Bitir' butonuna basıldığında veya arama bittiğinde."""
            self.call_status_label.configure(text="Arama sonlandırıldı.")
            self.end_call_button.pack_forget()  # Bitir butonunu gizle
            self.call_button.pack(side="right", padx=5)  # Ara butonunu göster
            self.master_app.run_coroutine_threadsafe(self.rtc_manager.close())

            if notify_server:
                # Ana uygulamaya (ChatApp) sunucuya göndermesi için sinyal ver
                self.master_app.send_call_signal("CALL_ENDED", self.target_username)

        # --- DIŞARIDAN KONTROL FONKSİYONLARI ---
        # Bu fonksiyonlar ana ChatApp tarafından çağrılacak

    # PrivateChatWindow sınıfı içinde
    def toggle_video(self):
        # Debounce: bir işlem zaten bekliyorsa ikinciyi başlatma
        if self.video_state in ("pending_incoming", "pending_outgoing"):
            self.call_status_label.configure(text="📷 Görüntülü arama isteği beklemede...")
            return

        if not self.video_enabled:
            # İstek yolla, kabul bekle
            self.video_state = "pending_outgoing"
            self.master_app.send_call_signal("VIDEO_REQUEST", self.target_username)
            self.video_button.configure(text="📷 Kapat")
        else:
            # Kapat
            self.video_state = "idle"
            self.video_enabled = False
            self.master_app.send_call_signal("VIDEO_ENDED", self.target_username)
            self.master_app.run_coroutine_threadsafe(self.rtc_manager.remove_camera_track())
            self.master_app.run_coroutine_threadsafe(self.rtc_manager.renegotiate())
            self.call_status_label.configure(text="📷 Görüntülü arama kapatıldı")
            self.video_button.configure(text="📷 Kamera")

    def on_video_request(self):
        """Gelen görüntülü arama isteğini Header üzerinde gösterir."""
        # Zaten aktifse veya işlem yapılıyorsa yoksay
        if self.video_state in ("active", "pending_outgoing"):
            self.master_app.send_call_signal("VIDEO_REJECT", self.target_username)
            return

        self.video_state = "pending_incoming"
        self.call_status_label.configure(text="🎥 Görüntülü Arama İsteği...", text_color="#F1C40F")

        # 1. Mevcut butonları gizle (Header temizlensin)
        self.call_button.pack_forget()
        self.video_button.pack_forget()
        self.end_call_button.pack_forget()

        # 2. Kabul/Red Butonları için geçici bir alan oluştur
        if self._video_dialog_buttons is None:
            self._video_dialog_buttons = ctk.CTkFrame(self.header_frame, fg_color="transparent")
            self._video_dialog_buttons.pack(side="right", padx=5)

            # Reddet Butonu
            self.btn_reject = ctk.CTkButton(self._video_dialog_buttons, text="Reddet", width=70,
                                            fg_color="#c0392b", hover_color="#e74c3c",
                                            command=self.reject_video)
            self.btn_reject.pack(side="right", padx=5)

            # Kabul Et Butonu
            self.btn_accept = ctk.CTkButton(self._video_dialog_buttons, text="Kabul Et", width=70,
                                            fg_color="#27ae60", hover_color="#2ecc71",
                                            command=self.accept_video)
            self.btn_accept.pack(side="right", padx=5)

            # Zil sesi çal (Opsiyonel)
            self.master_app.play_incoming_sound()



    def accept_video(self):
        """Gelen isteği kabul eder ve video alanını açar."""
        if self.video_state != "pending_incoming":
            return

        # 1. Butonları temizle
        self._dispose_video_dialog_buttons()

        # 2. Siyah Video Alanını Görünür Yap (Header ile Chat arasına)
        self.video_frame.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)
        self.video_frame.configure(height=240) # Video yüksekliği

        # 3. WebRTC Sinyalleri
        self.master_app.send_call_signal("VIDEO_ACCEPT", self.target_username)
        self.master_app.run_coroutine_threadsafe(self.rtc_manager.renegotiate())
        self.master_app.run_coroutine_threadsafe(self.rtc_manager.add_camera_track())

        # 4. Durum Güncellemesi
        self.video_enabled = True
        self.video_state = "active"
        self.call_status_label.configure(text="Bağlanıyor...", text_color="#2ecc71")

    def on_video_accepted_by_peer(self):
        if self.video_state != "pending_outgoing":
            return

        # 1. Arayan taraf olarak kameramızı ekliyoruz.
        self.master_app.run_coroutine_threadsafe(self.rtc_manager.add_camera_track())


        # 2. Müzakereyi (renegotiate) BİZ BAŞLATMIYORUZ.
        #    Aramayı kabul eden (alıcı) tarafın bize OFFER göndermesini bekleyeceğiz.


        # 3. Durumu "aktif" olarak ayarla
        self.video_enabled = True
        self.video_state = "active"
        self.call_status_label.configure(text="📷 Görüntülü arama başladı (Bağlanıyor...)")
        self.video_button.configure(text="📷 Kamera")


    def on_video_rejected_by_peer(self):
        if self.video_state != "pending_outgoing":
            return
        self.video_state = "idle"
        self.video_enabled = False
        self.call_status_label.configure(text="📷 Görüntülü arama reddedildi")
        self.video_button.configure(text="📷 Kamera")

    def reject_video(self):
        """İsteği reddeder ve arayüzü sıfırlar."""
        if self.video_state != "pending_incoming":
            return

        self.master_app.send_call_signal("VIDEO_REJECT", self.target_username)

        self.video_state = "idle"
        self.call_status_label.configure(text="Arama reddedildi.", text_color="#e74c3c")

        # Butonları temizle ve eski haline dön
        self._dispose_video_dialog_buttons()

    def _dispose_video_dialog_buttons(self):
        """Kabul/Red butonlarını temizler ve standart arayüzü geri yükler."""
        if self._video_dialog_buttons:
            try:
                self._video_dialog_buttons.destroy()
            except:
                pass
            self._video_dialog_buttons = None

        # Eğer video aktif değilse standart butonları geri getir
        if not self.video_enabled:
            self.video_button.configure(text="📹")
            self.call_button.pack(side="right", padx=10)
            self.video_button.pack(side="right", padx=(0, 5))
        else:
            # Video aktifse sadece Bitir butonu veya Video Kapat butonu kalsın
            self.video_button.configure(text="📷 Kapat")
            self.video_button.pack(side="right", padx=(0, 5))

    def on_call_accepted(self):
        """SADECE ARAYAN KİŞİ tarafından (kabul bildirimi alındığında) çağrılır."""

        # 1. Arayüzü "arama-içi" duruma geçir
        self.set_call_ui_to_active()

        # 2. El sıkışmayı (handshake) başlatmak için bir 'Teklif' (Offer) oluştur
        print(f"DEBUG ({self.target_username}): Arama kabul edildi, P2P 'Teklif' (Offer) gönderiliyor...")
        self.master_app.run_coroutine_threadsafe(self.rtc_manager.create_offer())

    def on_call_rejected(self):
            """Karşı taraf aramayı reddettiğinde."""
            self.call_status_label.configure(text="Arama reddedildi.")
            self.end_call(notify_server=False)  # Sadece UI'ı sıfırla

            # ... PrivateChatWindow sınıfı içinde ...



    async def run_video(self, track):
        """
        Gelen video akışını alır ve CTkLabel'da (video_label) görüntüler.
        """
        print(f"DEBUG ({self.target_username}): run_video coroutine'i BAŞLADI. Video bekleniyor...")
        try:
            while True:
                frame = await track.recv()  # av.VideoFrame
                img = frame.to_ndarray(format="bgr24")
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(img)

                # Debug: kare bilgisi
                pts = getattr(frame, "pts", None)
                h, w = img.shape[:2]
                print(f"DEBUG ({self.target_username}): Yeni frame (pts={pts}) size=({w}x{h})")

                def update_gui(pil_img_copy=pil_img):
                    try:
                        # CTkImage kullan → HighDPI destekli
                        tk_img = CTkImage(light_image=pil_img_copy, size=(w, h))
                        self.video_label.configure(image=tk_img)
                        self.video_label.image = tk_img  # referansı sakla
                    except Exception as e:
                        print(f"DEBUG ({self.target_username}): GUI güncelleme hatası: {e}")

                # Ana thread'te GUI güncellemesi
                self.master_app.schedule_gui_update(update_gui)

        except asyncio.CancelledError:
            print(f"DEBUG ({self.target_username}): run_video coroutine'i durduruldu.")
        except Exception as e:
            print(f"DEBUG ({self.target_username}): Video akışı durdu veya hata verdi: {e}")

            def clear_video_label():
                try:
                    self.video_label.configure(image=None)
                    self.video_label.image = None
                except:
                    pass

            self.master_app.schedule_gui_update(clear_video_label)

    def on_call_ended_by_peer(self):
            """Karşı taraf aramayı kapattığında."""
            self.call_status_label.configure(text="Karşı taraf kapattı.")
            self.end_call(notify_server=False)  # Sadece UI'ı sıfırla

    def send_message_event(self, event=None):
        message = self.message_entry.get()
        if not message:
            return

        # Ana uygulama üzerinden mesajı gönder
        self.master_app.send_dm_from_window(self.target_username, message)
        self.add_message_to_window(f"[Siz -> {self.target_username}]: {message}")

        # Kendi penceremize "Siz" olarak mesajı ekle

        self.message_entry.delete(0, "end")

    def add_message_to_window(self, message):
        """
        Mesajları baloncuk (bubble) tasarımıyla DM penceresine ekler.
        """
        # Pencere kapatılmışsa işlem yapma (Hata koruması)
        if not self.winfo_exists():
            return

        # Renk Ayarları
        colors = {
            "own_bg": "#005c4b",  # Yeşilimsi (Biz)
            "other_bg": "#363636",  # Gri (Karşı taraf)
            "text": "#e9edef"
        }

        # Mesajı Analiz Et (Kimden Geldi?)
        is_own_message = "[Siz ->" in message

        bubble_color = colors["own_bg"] if is_own_message else colors["other_bg"]
        # Pack kullanırken sağa/sola yaslamak için 'anchor' kullanılır
        anchor_val = "e" if is_own_message else "w"

        try:
            # --- DÜZELTME BURADA ---
            # Baloncuk Çerçevesi
            # .grid() YERİNE .pack() KULLANIYORUZ
            bubble_wrapper = ctk.CTkFrame(self.chat_box, fg_color="transparent")
            bubble_wrapper.pack(anchor=anchor_val, padx=10, pady=5, fill="x")
            # -----------------------

            msg_label = ctk.CTkLabel(bubble_wrapper, text=message,
                                     fg_color=bubble_color,
                                     text_color=colors["text"],
                                     corner_radius=16,
                                     wraplength=250,
                                     justify="left",
                                     padx=12, pady=8,
                                     font=("Roboto", 12))
            msg_label.pack()

            # Kaydırma işlemini güvenli hale getir
            def safe_scroll():
                try:
                    if self.winfo_exists():
                        self.chat_box._parent_canvas.yview_moveto(1.0)
                except:
                    pass

            self.after(50, safe_scroll)

        except Exception as e:
            print(f"DM baloncuk hatası: {e}")


    def on_closing(self):
        """
        Pencere kapatıldığında, ana uygulamanın sözlüğünden
        kendini kaldırır.
        """
        self.master_app.run_coroutine_threadsafe(self.rtc_manager.close())
        self.master_app.notify_private_window_closed(self.target_username)
        self.destroy()

class ChatApp(ctk.CTk):
    def __init__(self):

        super().__init__()
        self.title("Şifreli Chat (Asyncio/WebSocket Sürümü)")
        self.geometry("450x600")

        self.e2ee_sessions = {}
        # --- Asyncio ve Threading Köprüsü ---
        self.audio_frames = []

        # --- Durum Değişkenleri ---
        self.websocket = None  # Artık 'client_socket' değil
        self.nickname = ""
        self.authenticated = False

        self.private_chat_windows = {}
        # --- SOUNDDEVICE/SES AYARLARI ---
        self.audio_stream_in = None  # Kayıt stream'i
        self.audio_stream_out = None  # Çalma stream'i
        self.is_recording = False

        self.channels = 1
        self.dtype = 'int16'  # Bu, pyaudio.paInt16'nın NumPy karşılığıdır
        self.chunk = 1024

        try:
            # Doğru sorgu: 'query_devices(kind='input')['default_samplerate']'
            self.rate = int(sd.query_devices(kind='input')['default_samplerate'])
        except Exception as e:
            print(f"UYARI: Varsayılan mikrofon bulunamadı, 44100Hz varsayılıyor. Hata: {e}")
            self.rate = 44100  # Güvenli bir varsayılan

        self.MAX_RECORD_SECONDS = 10

        # --- YENİ SATIRLAR ---
        self._typing_timer = None  # 3 saniyelik "yazmayı bıraktı" zamanlayıcısı
        self._am_i_typing = False  # Sunucuya gereksiz 'START' komutu göndermemek için
        self.who_is_typing = set()  # Kimlerin yazdığını tutan liste
        # --- YENİ SATIRLAR SONU ---
        # --- Asyncio ve Threading Köprüsü ---
        self.asyncio_loop = asyncio.new_event_loop()  # Arka plan thread'i için yeni bir event loop
        self.queue = asyncio.Queue()  # Arayüzden -> Asyncio'ya komut göndermek için
        self._sound_cooldown_timer_in = None  # Gelen mesajlar için
        self._sound_cooldown_timer_out = None  # Giden mesajlar için
        # --- GÜNCELLENMİŞ KISIM ---
        self.load_icons()  # İkonları yükle
        self.start_asyncio_thread()
        self.create_auth_ui()
        # --- GÜNCELLENMİŞ KISIM SONU ---

    def send_encrypted_video(self, target_username, file_path):
        """
        Seçilen video dosyasını okur, E2EE ile şifreler ve gönderir.
        """
        # 1. Hedef ile şifreli oturum var mı kontrol et
        sess = self.e2ee_sessions.get(target_username)
        if not sess or "aes_key" not in sess:
            print(f"HATA: {target_username} ile şifreli oturum yok. Önce mesajlaşın.")
            return

        # İşlem uzun sürebileceği için arka plan thread'inde çalıştıralım
        threading.Thread(target=self._send_encrypted_video_thread,
                         args=(target_username, file_path, sess)).start()

    def _send_encrypted_video_thread(self, target_username, file_path, sess):
        try:
            import base64
            from crypto_e2ee import seal  # Sizin kütüphanenizdeki şifreleme fonksiyonu

            print(f"DEBUG: Video okunuyor: {file_path}")

            # 2. Dosyayı Binary (Byte) olarak oku
            with open(file_path, "rb") as f:
                video_data = f.read()

            # (Opsiyonel) Burada ffmpeg ile video boyutunu küçültebilirsiniz.
            # Ancak şimdilik doğrudan şifrelemeye odaklanalım.

            print("DEBUG: Video şifreleniyor...")

            # 3. E2EE Şifreleme (AES-GCM)
            # AAD (Additional Authenticated Data) ile veriyi bağlayalım
            aad = f"[{self.nickname}->{target_username}:VIDEO]".encode("utf-8")

            # seal fonksiyonu (nonce, ciphertext) döndürür
            nonce, ciphertext = seal(sess["aes_key"], video_data, aad=aad)

            # 4. Veriyi JSON uyumlu Base64 formatına çevir
            payload_json = {
                "command": "ENC_FILE_MSG",  # Yeni bir komut türü tanımladık
                "payload": {
                    "target": target_username,
                    "type": "video",
                    "ext": "mp4",  # Uzantıyı dinamik almanız daha iyi olur
                    "nonce": base64.b64encode(nonce).decode("utf-8"),
                    "salt": base64.b64encode(sess["salt"]).decode("utf-8"),
                    "ct": base64.b64encode(ciphertext).decode("utf-8"),
                    "aad": base64.b64encode(aad).decode("utf-8"),
                }
            }

            # 5. Sunucuya Gönder
            self.run_coroutine_threadsafe(self.send_json_to_server(payload_json))
            print("DEBUG: Şifreli video gönderildi.")

            # Sohbet penceresine bilgi düş
            self.schedule_gui_update(
                self.add_message_to_chatbox, "DM", f"[Siz -> {target_username}]: 📹 (Şifreli Video Gönderildi)"
            )

        except Exception as e:
            print(f"Video gönderme hatası: {e}")

        # ChatApp sınıfı içinde
    def send_call_signal(self, command, target_user, data_payload=None):
            """Genel amaçlı arama sinyali gönderici. (Güncellendi)"""

            # Temel yükü (payload) oluştur
            payload_content = {"target": target_user}

            # Eğer ekstra veri (örn: SDP) varsa, onu da yüke ekle
            if data_payload:
                payload_content.update(data_payload)

            payload_json = {"command": command, "payload": payload_content}
            self.run_coroutine_threadsafe(self.send_json_to_server(payload_json))

    def open_private_chat(self, target_username):
        """Kullanıcı listesinden birine tıklandığında çağrılır."""
        if not target_username or target_username == "None":
            return

        if target_username == self.nickname:
            return

        # 1. Pencere zaten sözlükte kayıtlı mı?
        if target_username in self.private_chat_windows:
            window = self.private_chat_windows[target_username]

            # 2. Pencere Tkinter tarafında gerçekten yaşıyor mu?
            try:
                if window.winfo_exists():
                    window.lift()  # Pencereyi öne getir
                    window.focus_force()  # Odağı pencereye ver
                else:
                    # Listede var ama aslında kapatılmış (TclError riski)
                    raise Exception("Pencere ölü")
            except Exception:
                # Hata alırsak (pencere kapanmışsa), listeden temizle ve yeniden açmayı dene
                print(f"DEBUG: {target_username} penceresi ölü, listeden temizleniyor...")
                self.private_chat_windows.pop(target_username, None)
                self.open_private_chat(target_username)  # Fonksiyonu tekrar çağır (Recursive)

        else:
            # Listede yoksa yeni pencere oluştur
            try:
                new_window = PrivateChatWindow(master=self, target_username=target_username)
                self.private_chat_windows[target_username] = new_window
                self.start_e2ee_handshake_with(target_username)

                # Geçmiş mesajları iste
                payload_json = {
                    "command": "FETCH_DM_HISTORY",
                    "payload": {"target": target_username}
                }
                self.run_coroutine_threadsafe(self.send_json_to_server(payload_json))

            except Exception as e:
                print(f"Özel pencere oluşturulamadı: {e}")

    def send_dm_from_window(self, target_user, message):

        sess = self.e2ee_sessions.get(target_user)
        if sess and "aes_key" in sess:
            from crypto_e2ee import seal
            aad = f"[{self.nickname}->{target_user}]".encode("utf-8")
            nonce, ct = seal(sess["aes_key"], message.encode("utf-8"), aad=aad)
            payload_json = {
                "command": "ENC_MSG",
                "payload": {
                    "target": target_user,
                    "nonce": base64.b64encode(nonce).decode("utf-8"),
                    "salt": base64.b64encode(sess["salt"]).decode("utf-8"),
                    "ct": base64.b64encode(ct).decode("utf-8"),
                    "aad": base64.b64encode(aad).decode("utf-8"),
                }
            }
        else:
            payload_json = {"command": "DM", "payload": {"target": target_user, "message": message}}

        self.run_coroutine_threadsafe(self.send_json_to_server(payload_json))
        self.play_outgoing_sound()

    def notify_private_window_closed(self, target_username):
        """Özel pencere kapatıldığında çağrılır."""
        self.private_chat_windows.pop(target_username, None)
        print(f"DEBUG: {target_username} ile özel sohbet kapatıldı.")

    def load_icons(self):
        """Uygulama için gerekli ikonları yükler."""
        try:

            self.user_icon = ctk.CTkImage(Image.open(resource_path("assets/user_icon.png")), size=(24, 24))
            self.lock_icon = ctk.CTkImage(Image.open(resource_path("assets/lock_icon.png")), size=(24, 24))
            self.send_icon = ctk.CTkImage(Image.open(resource_path("assets/send_icon.png")), size=(24, 24))
            self.server_icon = ctk.CTkImage(Image.open(resource_path("assets/server_icon.png")), size=(24, 24))
        except FileNotFoundError as e:
            print(f"Hata: İkon dosyaları 'assets' klasöründe bulunamadı: {e}")
            print("İkonsuz devam ediliyor...")
            # Hata durumunda boş ikonlar oluştur
            self.user_icon = None
            self.lock_icon = None
            self.server_icon = None
            self.send_icon = None

    # --- YENİ FONKSİYON SONU ---

    def start_asyncio_thread(self):
        """Asyncio event loop'u ayrı bir thread'de başlatır."""

        def run_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()

        t = threading.Thread(target=run_loop, args=(self.asyncio_loop,), daemon=True)
        t.start()
        print("DEBUG: Asyncio arka plan thread'i başlatıldı.")

    def run_coroutine_threadsafe(self, coro):
        """Ana thread'den (GUI) asyncio thread'ine güvenle coroutine göndermeyi sağlar."""
        return asyncio.run_coroutine_threadsafe(coro, self.asyncio_loop)

    def schedule_gui_update(self, func, *args, **kwargs):
        """
        Asyncio thread'inden ana GUI thread'ine güvenle fonksiyon çağırmayı sağlar.
        TclError (bad window path) hatalarını susturur.
        """
        def safe_wrapper():
            try:
                # Asıl fonksiyonu çalıştır
                func(*args, **kwargs)
            except tkinter.TclError:
                # "bad window path name" gibi hataları yut (Programı durdurma)
                pass
            except Exception as e:
                # Diğer ciddi hataları yazdır
                print(f"GUI Görev Hatası: {e}")

        # Lambda yerine bu güvenli paketleyiciyi kullan
        self.after(0, safe_wrapper)

    # --- Arayüz Fonksiyonları (Çoğunlukla Aynı) ---

        # 'create_auth_ui' fonksiyonunuzu TAMAMEN bununla değiştirin:
        # ChatApp sınıfının içine, diğer def fonksiyonlarıyla aynı hizaya EKLE:

    def show_auth_error(self, message):
            """Giriş/Kayıt ekranındaki hata etiketini günceller."""
            try:
                # Not: Bu fonksiyon, mesaj 'başarılı' içeriyorsa rengi yeşile çevirir
                self.auth_error_label.configure(text=message,
                                                text_color="red" if "başarılı" not in message else "green")
            except:
                # Arayüz (etiket) artık mevcut değilse (çok nadir) görmezden gel
                pass

    def create_auth_ui(self):
        """
        Modern, ortalanmış 'Login Card' tasarımı.
        """
        self.clear_widgets()
        self.geometry("400x600")
        self.title("Giriş Yap")
        self.configure(fg_color="#1a1a1a")  # Ana arka plan (Çok koyu)

        # Ana Grid (Ortalamak için)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # --- ORTA KART (Card) ---
        self.auth_card = ctk.CTkFrame(self, fg_color="#2b2b2b", corner_radius=20, width=320)
        self.auth_card.grid(row=0, column=0, padx=20, pady=20)
        self.auth_card.grid_columnconfigure(0, weight=1)

        # Logo / Başlık
        ctk.CTkLabel(self.auth_card, text="🔐", font=("Arial", 40)).pack(pady=(30, 10))
        ctk.CTkLabel(self.auth_card, text="Secure Chat", font=("Roboto", 24, "bold"), text_color="white").pack(
            pady=(0, 20))

        # Sekmeli Yapı (Tabview) - Kartın içine
        self.tab_view = ctk.CTkTabview(self.auth_card, width=280, height=300,
                                       fg_color="transparent",
                                       segmented_button_fg_color="#1a1a1a",
                                       segmented_button_selected_color="#3B8ED0",
                                       segmented_button_unselected_color="#1a1a1a")
        self.tab_view.pack(pady=10, padx=10)

        self.tab_view.add("Giriş Yap")
        self.tab_view.add("Kayıt Ol")

        # --- GİRİŞ YAP SEKMESİ ---
        login_frame = self.tab_view.tab("Giriş Yap")

        self.username_entry_login = ctk.CTkEntry(login_frame, placeholder_text="Kullanıcı Adı", width=250, height=40,
                                                 corner_radius=10)
        self.username_entry_login.pack(pady=15)

        self.password_entry_login = ctk.CTkEntry(login_frame, placeholder_text="Şifre", show="*", width=250, height=40,
                                                 corner_radius=10)
        self.password_entry_login.pack(pady=10)

        self.login_button = ctk.CTkButton(login_frame, text="GİRİŞ YAP", width=250, height=40, corner_radius=10,
                                          fg_color="#27ae60", hover_color="#2ecc71",
                                          font=("Roboto", 14, "bold"),
                                          command=self.handle_login)
        self.login_button.pack(pady=20)

        # --- KAYIT OL SEKMESİ ---
        register_frame = self.tab_view.tab("Kayıt Ol")

        self.username_entry_register = ctk.CTkEntry(register_frame, placeholder_text="Kullanıcı Adı Seç", width=250,
                                                    height=35, corner_radius=10)
        self.username_entry_register.pack(pady=10)

        self.password_entry_register = ctk.CTkEntry(register_frame, placeholder_text="Şifre Belirle", show="*",
                                                    width=250, height=35, corner_radius=10)
        self.password_entry_register.pack(pady=5)

        self.password_entry_confirm = ctk.CTkEntry(register_frame, placeholder_text="Şifre Tekrar", show="*", width=250,
                                                   height=35, corner_radius=10)
        self.password_entry_confirm.pack(pady=5)

        self.register_button = ctk.CTkButton(register_frame, text="HESAP OLUŞTUR", width=250, height=40,
                                             corner_radius=10,
                                             fg_color="#2980b9", hover_color="#3498db",
                                             font=("Roboto", 14, "bold"),
                                             command=self.handle_register)
        self.register_button.pack(pady=20)

        # --- ALT BİLGİ (Sunucu Ayarı) ---
        self.server_frame = ctk.CTkFrame(self.auth_card, fg_color="transparent")
        self.server_frame.pack(pady=10)

        self.server_entry = ctk.CTkEntry(self.server_frame, width=120, height=25, placeholder_text="IP")
        self.server_entry.insert(0, "127.0.0.1")
        self.server_entry.pack(side="left", padx=5)

        self.port_entry = ctk.CTkEntry(self.server_frame, width=60, height=25, placeholder_text="Port")
        self.port_entry.insert(0, "50505")
        self.port_entry.pack(side="left", padx=5)

        self.auth_error_label = ctk.CTkLabel(self.auth_card, text="", text_color="#e74c3c", font=("Arial", 12))
        self.auth_error_label.pack(pady=(0, 20))

    def handle_login(self):
        """Giriş komutunu ve bağlantı bilgilerini hazırlar, async işleyiciye gönderir."""
        username = self.username_entry_login.get()
        password = self.password_entry_login.get()
        host = self.server_entry.get()
        port = self.port_entry.get()

        if not username or not password or not host or not port:
            self.show_auth_error("Tüm alanlar doldurulmalıdır.")
            return

        # --- EKLENEN BLOK ---
        # Hata 1'i düzeltir: Butonları kilitle ve geri bildirim ver
        self.set_auth_buttons_state("disable")
        self.show_auth_error("Giriş yapılıyor...")
        # --- EKLENEN BLOK SONU ---

        # Sunucuya gönderilecek İLK komutu hazırla
        payload_json = {"command": "LOGIN", "payload": {"user": username, "pass": password}}

        # Async motora "Bağlan ve bu ilk komutu gönder" görevini ver
        self.run_coroutine_threadsafe(self.connect_and_process(host, port, payload_json))


    def handle_register(self):
        username = self.username_entry_register.get()
        password = self.password_entry_register.get()
        confirm = self.password_entry_confirm.get()
        host = self.server_entry.get()
        port = self.port_entry.get()

        if not username or not password or not confirm or not host or not port: self.show_auth_error("Tüm alanlar doldurulmalıdır."); return
        if password != confirm: self.show_auth_error("Şifreler uyuşmuyor."); return
        if len(password.encode('utf-8')) > 72: self.show_auth_error("Şifre çok uzun (Maks. 72 byte)."); return

        self.set_auth_buttons_state("disable")
        self.show_auth_error("Bağlanılıyor...")

        payload_json = {"command": "REGISTER", "payload": {"user": username, "pass": password}}

    # KRİTİK DÜZELTME: Doğru fonksiyon adı kullanılmalı
        self.run_coroutine_threadsafe(self.connect_and_process(host, port, payload_json))



    async def send_json_to_server(self, data):
        """JSON verisini string'e çevirir ve websocket üzerinden gönderir."""
        # 'if self.websocket:' kontrolünü kaldırıyoruz,
        # çünkü bu fonksiyon artık sadece 'websocket'in var olduğu
        # güvenli bir bağlamda (context) çağrılacak.
        try:
            await self.websocket.send(json.dumps(data))
        except Exception as e:
            # Bağlantı tam o anda koptuysa
            print(f"HATA: Gönderilemedi, bağlantı muhtemelen kapandı: {e}")
            self.schedule_gui_update(self.go_back_to_login, "Bağlantı koptu, gönderilemedi.")

    async def connect_and_process(self, host, port, initial_payload_json):
        """Sunucuya bağlanır, İLK komutu gönderir ve dinlemeye başlar."""

        # --- wss:// kullan--
        uri = f"wss://{host}:{port}"

        # --- DÜZELTME 2: 'ws://' 'ssl=None' gerektirir. ---
        # 'ssl_context' oluşturan tüm satırları siliyoruz
        # ve 'ssl_param'i manuel olarak 'None' yapıyoruz.
        ssl_param = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_param.check_hostname = False
        ssl_param.verify_mode = ssl.CERT_NONE

        print("DEBUG: Güvensiz (ws://) bağlantı deneniyor...")

        try:
            # --- DÜZELTME 3: 'ssl=ssl_context' yerine 'ssl=ssl_param' (None) kullanın ---
            async with websockets.connect(uri, ssl=ssl_param) as websocket:
                self.websocket = websocket
                print(f"DEBUG: {uri} adresine bağlanıldı.")
                self.schedule_gui_update(self.show_auth_error, "Bağlanıldı, giriş yapılıyor...")

                # 2. Bağlantı TAMAMLANDIKTAN SONRA, ilk komutu gönder
                await self.send_json_to_server(initial_payload_json)

                # 3. Komut gönderildikten SONRA, cevapları dinlemeye başla
                async for message in websocket:
                    self.schedule_gui_update(self.handle_server_message, message)

        except asyncio.TimeoutError:
            self.schedule_gui_update(self.go_back_to_login, "Bağlantı zaman aşımına uğradı (Sunucu/Firewall).")
        except websockets.exceptions.InvalidURI:
            self.schedule_gui_update(self.go_back_to_login, "Hata: Geçersiz Sunucu Adresi/Portu.")
        # WSS/SSL el sıkışma hatası
        except ssl.SSLError as e:
            print(f"SSL Hatası: {e}", file=sys.stderr)
            self.schedule_gui_update(self.go_back_to_login, "Güvenlik (SSL) hatası. Sunucu sertifikası geçersiz.")
        except (OSError, websockets.exceptions.ConnectionClosed) as e:
            print(f"DEBUG: Bağlantı kesildi veya kurulamadı: {e}")
            self.schedule_gui_update(self.go_back_to_login, f"Sunucuya bağlanılamadı: {e}")
        except Exception as e:
            print(f"DEBUG: Beklenmedik websocket hatası: {e}")
            traceback.print_exc(file=sys.stderr)
            self.schedule_gui_update(self.go_back_to_login, f"Bilinmeyen bir hata oluştu: {e}")
        finally:
            # Dinleme döngüsü biterse (bağlantı koparsa)
            self.websocket = None
            self.authenticated = False
            print("DEBUG: connect_and_process sonlandı, bağlantı sıfırlandı.")
            self.schedule_gui_update(self.set_auth_buttons_state, "normal")

    def handle_server_message(self, message_str):
        """Sunucudan gelen JSON mesajını (string) ayrıştırır ve ilgili GUI fonksiyonunu çağırır."""
        global derive_aes_key
        try:
            data = json.loads(message_str)
            command = data.get("command")
            payload = data.get("payload")

            # Sunucunun yeni protokolüne (JSON) göre yönlendirme

            if command == "LOGIN_DATA_PACKAGE":
                self.schedule_gui_update(self.set_auth_buttons_state, "normal")
                self.transition_to_chat(payload)


            elif command in ["LOGIN_FAIL", "REGISTER_SUCCESS", "REGISTER_FAIL","AUTH_FAIL"]:

                if command == "REGISTER_SUCCESS":

                    self.show_auth_error(f"{payload} Lütfen şimdi giriş yapın.")

                else:

                    self.show_auth_error(payload)

                self.set_auth_buttons_state("normal")


            if command == "TYPING_START":
                self.update_typing_status(payload, is_typing=True)  # payload = "username"

            elif command == "TYPING_STOP":
                self.update_typing_status(payload, is_typing=False)  # payload = "username"
            # --- YENİ BLOKLAR SONU ---
            elif command == "KICK_SIGNAL":
                # --- KRİTİK EKLENTİ ---
                self.add_message_to_chatbox("SYS_MSG_ERR", payload)  # Atılma mesajını göster
                # Sıfırlamayı ana GUI thread'ine taşıyarak sorunsuz geçişi garantile
                self.schedule_gui_update(self.go_back_to_login, "Sunucudan atıldınız. Lütfen tekrar bağlanın.")
                # --- EKLENTİ SONU ---
            elif command == "AUDIO_DATA":
                file_id = payload.get("file_id")
                ct_b64 = payload.get("filedata_b64")
                ct = base64.b64decode(ct_b64)

                nonce_b64 = payload.get("nonce")
                salt_b64 = payload.get("salt")
                aad_b64 = payload.get("aad")

                if nonce_b64 and salt_b64:
                    nonce = base64.b64decode(nonce_b64)
                    salt = base64.b64decode(salt_b64)
                    aad = base64.b64decode(aad_b64) if aad_b64 else b""

                    sess = self.e2ee_sessions.get(target_user)
                    from crypto_e2ee import open_, derive_aes_key
                    if sess["salt"] != salt:
                        shared = sess["my_priv"].exchange(sess["peer_pub"])
                        sess["aes_key"] = derive_aes_key(shared, salt, self.e2ee_info_for_peer(target_user))
                        sess["salt"] = salt

                    try:
                        audio_bytes = open_(sess["aes_key"], nonce, ct, aad=aad)
                        self.play_audio_chunk(audio_bytes)
                    except Exception as e:
                        self.add_message_to_chatbox("SYS_MSG_ERR", f"E2E ses çözme hatası: {e}")
                else:
                    # fallback: şifresiz
                    self.play_audio_chunk(ct)
            # --- YENİ BLOK SONU ---
            elif command == "CALL_REQUEST":
                # Birisi bizi arıyor (Modern Popup)
                sender = payload.get("from")
                if sender:
                    # Eski 'CTkInputDialog' yerine yeni özel pencereyi çağırıyoruz
                    # Lambda kullanarak GUI thread'inde güvenle açılmasını sağlıyoruz
                    self.schedule_gui_update(self.show_incoming_call_dialog, sender)



            elif command == "CALL_ACCEPT":
                # Aradığımız kişi kabul etti
                sender = payload.get("from")
                if sender in self.private_chat_windows:
                    self.private_chat_windows[sender].on_call_accepted()

            elif command == "CALL_REJECT":
                # Aradığımız kişi reddetti
                sender = payload.get("from")
                if sender in self.private_chat_windows:
                    self.private_chat_windows[sender].on_call_rejected()

            elif command == "CALL_ENDED":
                # Karşı taraf kapattı
                sender = payload.get("from")
                if sender in self.private_chat_windows:
                    self.private_chat_windows[sender].on_call_ended_by_peer()


            elif command == "CALL_OFFER":

                # Birinden 'Teklif' (Offer) aldık (Biz 'Aranan' kişiyiz)

                sender = payload.get("from")

                sdp_data = payload.get("sdp")

                # İlgili pencerenin açık olduğundan emin ol

                if sender not in self.private_chat_windows:
                    self.open_private_chat(sender)

                if sender in self.private_chat_windows and sdp_data:
                    print(f"DEBUG ({sender}): 'Teklif' (Offer) alındı, 'Cevap' (Answer) hazırlanıyor...")

                    # İlgili pencerenin yöneticisine teklifi işlettir (Bu, 'Cevap' gönderecek)

                    rtc_manager = self.private_chat_windows[sender].rtc_manager

                    self.run_coroutine_threadsafe(rtc_manager.handle_offer(sdp_data))


            elif command == "CALL_ANSWER":

                # Gönderdiğimiz 'Teklif'e 'Cevap' (Answer) aldık (Biz 'Arayan' kişiyiz)

                sender = payload.get("from")

                sdp_data = payload.get("sdp")

                if sender in self.private_chat_windows and sdp_data:
                    print(f"DEBUG ({sender}): 'Cevap' (Answer) alındı. P2P kuruluyor...")

                    # İlgili pencerenin yöneticisine cevabı işlettir

                    rtc_manager = self.private_chat_windows[sender].rtc_manager

                    self.run_coroutine_threadsafe(rtc_manager.handle_answer(sdp_data))




            elif command == "CALL_CANDIDATE":

                sender = payload.get("from")

                # HATA 1 DÜZELTİLDİ: 'candidate' -> 'sdp'

                candidate_sdp = payload.get("sdp")

                if sender in self.private_chat_windows and candidate_sdp:
                    rtc_manager = self.private_chat_windows[sender].rtc_manager

                    # HATA 2 DÜZELTİLDİ: Çağrı async ve rtc_manager üzerinden olmalı

                    self.run_coroutine_threadsafe(

                        rtc_manager.add_ice_candidate_sdp(candidate_sdp)

                    )

            elif command == "DM_HISTORY":
                target = payload.get("target")
                history = payload.get("messages", [])
                if target in self.private_chat_windows:
                    window = self.private_chat_windows[target]
                    for msg in history:
                        window.add_message_to_window(msg)

            elif command == "KEY_INIT":
                sender = payload.get("from_user")
                peer_pub_b64 = payload.get("pub")
                salt_b64 = payload.get("salt")
                # If we don't have a session, create ephemeral keys now
                if sender not in self.e2ee_sessions:
                    self.start_e2ee_handshake_with(sender)  # creates my_priv/my_pub/salt
                # Derive and send KEY_REPLY
                self.complete_e2ee_handshake(sender, peer_pub_b64, salt_b64)

            elif command == "KEY_REPLY":


                sender = payload.get("from_user")
                peer_pub_b64 = payload.get("pub")
                salt_b64 = payload.get("salt")
                # Derive final key; do not send reply (we initiated)

                sess = self.e2ee_sessions.get(sender)
                if sess:
                    peer_pub = pubkey_from_bytes(base64.b64decode(peer_pub_b64))
                    salt = base64.b64decode(salt_b64)
                    shared = sess["my_priv"].exchange(peer_pub)
                    key = derive_aes_key(shared, salt, self.e2ee_info_for_peer(sender))
                    sess.update({"peer_pub": peer_pub, "aes_key": key, "salt": salt})
                    self.add_message_to_chatbox("SYS_MSG", f"🔐 {sender} ile E2E tamamlandı.")


            elif command == "VIDEO_REQUEST":

                sender = payload.get("from")

                if sender not in self.private_chat_windows:
                    self.open_private_chat(sender)

                window = self.private_chat_windows.get(sender)

                if window:
                    window.on_video_request()


            elif command == "VIDEO_ACCEPT":

                sender = payload.get("from")

                window = self.private_chat_windows.get(sender)

                if window:
                    # Karşı tarafın kabulü bize geldiyse, dışa-dönük isteğimiz bekliyorsa ilerle

                    window.on_video_accepted_by_peer()


            elif command == "VIDEO_REJECT":

                sender = payload.get("from")

                window = self.private_chat_windows.get(sender)

                if window:
                    window.on_video_rejected_by_peer()


            elif command == "VIDEO_ENDED":

                sender = payload.get("from")

                window = self.private_chat_windows.get(sender)

                if window:
                    window.video_state = "idle"

                    window.video_enabled = False

                    self.run_coroutine_threadsafe(window.rtc_manager.remove_camera_track())

                    window.call_status_label.configure(text="📷 Görüntülü arama kapatıldı")

                    window.video_button.configure(text="📷 Kamera")



            elif self.authenticated:





                # Giriş yapıldıktan sonra gelen diğer komutlar
                if command == "USER_LIST_UPDATE":
                    self.update_online_list_ui(payload)  # payload = ["ahmet", "zeynep"]


                elif command == "CHAT" or command == "SYS_MSG" or command == "SYS_MSG_ERR":

                    self.add_message_to_chatbox(command, payload)


                elif command == "DM":

                    # Sunucu '[Gönderen -> Siz]: Mesaj' veya '[Siz -> Hedef]: Mesaj' formatında gönderir

                    other_username = None

                    try:

                        if payload.startswith("[Siz -> "):

                            # Bu, sizin gönderdiğiniz bir mesajın onayıdır

                            other_username = payload.split(' ', 3)[2].strip(']:')

                        elif payload.startswith("["):

                            # Bu, size gelen yeni bir mesajdır

                            other_username = payload.split(' ', 1)[0].strip('[')

                    except Exception as e:

                        print(f"DM yönlendirmesi için kullanıcı adı ayrıştırılamadı: {e}")

                    if other_username:

                        # Pencereyi aç veya öne getir

                        self.open_private_chat(other_username)

                        # Mesajı ilgili pencereye ekle

                        if other_username in self.private_chat_windows:
                            self.private_chat_windows[other_username].add_message_to_window(payload)

                        # Gelen mesaj sesi çal (Sadece bize geliyorsa)

                        if not payload.startswith("[Siz -> "):
                            self.play_incoming_sound()

                    else:

                        # Bir hata olursa, eski yöntem gibi ana pencereye bas

                        self.add_message_to_chatbox("SYS_MSG_ERR", f"DM hedefi ayrıştırılamadı: {payload}")


                elif command == "ENC_MSG":
                    # Decide peer by payload context: for DM, payload['from_user'] = sender, for public chat you may carry sender too.
                    sender = payload.get("from_user")
                    nonce = base64.b64decode(payload.get("nonce"))
                    salt = base64.b64decode(payload.get("salt"))
                    ct = base64.b64decode(payload.get("ct"))
                    aad_b64 = payload.get("aad")
                    aad = base64.b64decode(aad_b64) if aad_b64 else b""

                    sess = self.e2ee_sessions.get(sender)
                    if not sess or "aes_key" not in sess:
                        self.add_message_to_chatbox("SYS_MSG_ERR", f"E2E anahtarı yok: {sender}")
                        return

                    # Optional: verify salt matches session; if not, re-derive
                    if sess["salt"] != salt:
                        from crypto_e2ee import derive_aes_key
                        shared = sess["my_priv"].exchange(sess["peer_pub"])
                        sess["aes_key"] = derive_aes_key(shared, salt, self.e2ee_info_for_peer(sender))
                        sess["salt"] = salt

                    from crypto_e2ee import open_
                    try:
                        msg = open_(sess["aes_key"], nonce, ct, aad=aad).decode("utf-8")
                        # Render like normal DM
                        self.open_private_chat(sender)
                        if sender in self.private_chat_windows:
                            self.private_chat_windows[sender].add_message_to_window(f"[{sender} -> Siz]: {msg}")
                        else:
                            self.add_message_to_chatbox("DM", f"[{sender} -> Siz]: {msg}")

                        self.play_incoming_sound()
                    except Exception as e:
                        self.add_message_to_chatbox("SYS_MSG_ERR", f"E2E çözme hatası: {e}")

                        # ... mevcut ENC_MSG bloğundan sonra ...

                elif command == "ENC_AUDIO_MSG":
                    sender = payload.get("from_user")
                    nonce = base64.b64decode(payload.get("nonce"))
                    salt = base64.b64decode(payload.get("salt"))
                    ct = base64.b64decode(payload.get("ct"))
                    aad = base64.b64decode(payload.get("aad"))

                    sess = self.e2ee_sessions.get(sender)
                    if sess:
                        try:
                            from crypto_e2ee import open_
                            # Şifreyi Çöz
                            audio_bytes = open_(sess["aes_key"], nonce, ct, aad=aad)

                            # Çalınabilir hale getir (Thread içinde)
                            self.play_audio_chunk(audio_bytes)
                            self.add_message_to_chatbox("DM", f"[{sender} -> Siz]: 🎤 Şifreli Ses Çalınıyor...")
                        except Exception as e:
                            print(f"Ses çözme hatası: {e}")

                elif command == "AUDIO_MSG":
                    sender = payload.get("from_user", "Anonim")
                    file_id = payload.get("file_id")  # Sunucu bunu üretip yollamalı
                    duration = payload.get("duration_seconds", 0)

                    # Ekrana Oynatma Butonu Bas (ID ile)
                    msg_text = f"[▶️ Sesli Mesaj - ID: {file_id}]"
                    self.add_message_to_chatbox("CHAT", msg_text, sender)

                elif command == "ENC_FILE_MSG":
                        sender = payload.get("from_user")  # Sunucu bunu eklemeli
                        # Eğer sunucu 'from_user' eklemiyorsa, payload içindeki veriden çıkarım yapmalısınız.

                        # Verileri Çöz
                        nonce = base64.b64decode(payload.get("nonce"))
                        salt = base64.b64decode(payload.get("salt"))
                        ct = base64.b64decode(payload.get("ct"))
                        aad = base64.b64decode(payload.get("aad"))
                        file_ext = payload.get("ext", "mp4")

                        # Oturum Anahtarını Bul
                        sess = self.e2ee_sessions.get(sender)
                        if not sess:
                            self.add_message_to_chatbox("SYS_MSG_ERR", f"{sender} video gönderdi ama anahtar yok.")
                            return

                        try:
                            from crypto_e2ee import open_
                            # Şifreyi Çöz (Decryption)
                            decrypted_video_bytes = open_(sess["aes_key"], nonce, ct, aad=aad)

                            # Dosyayı Kaydet
                            timestamp = int(time.time())
                            filename = f"received_{sender}_{timestamp}.{file_ext}"

                            with open(filename, "wb") as f:
                                f.write(decrypted_video_bytes)

                            self.add_message_to_chatbox("DM", f"[{sender} -> Siz]: 📹 Şifreli Video Alındı: {filename}")

                            # İsterseniz burada otomatik olarak videoyu açabilirsiniz
                            # os.startfile(filename)

                        except Exception as e:
                            self.add_message_to_chatbox("SYS_MSG_ERR", f"Video şifresi çözülemedi: {e}")


            else:
                print(f"DEBUG: Kimlik doğrulanmamışken bilinmeyen komut: {command}")



        except json.JSONDecodeError:
            print(f"HATA: Sunucudan hatalı JSON alındı: {message_str}")

    def show_incoming_call_dialog(self, sender):
        """
        Özel tasarımlı, engelleyici olmayan gelen arama penceresi.
        """
        # Zil sesi çal
        self.play_incoming_sound()

        # Pencereyi Oluştur
        call_window = ctk.CTkToplevel(self)
        call_window.title("Gelen Arama")
        call_window.geometry("300x400")
        call_window.resizable(False, False)
        call_window.configure(fg_color="#222222")

        # Pencereyi en öne al
        call_window.attributes("-topmost", True)
        call_window.lift()

        # --- GÜVENLİK İÇİN EKLENEN PROTOKOL ---
        # Kullanıcı X ile kapatırsa reddetmiş sayalım
        def on_window_close():
            self.send_call_signal("CALL_REJECT", sender)
            call_window.destroy()

        call_window.protocol("WM_DELETE_WINDOW", on_window_close)
        # --------------------------------------

        # Profil / Avatar Kısmı
        ctk.CTkLabel(call_window, text="📞", font=("Arial", 60)).pack(pady=(50, 20))

        ctk.CTkLabel(call_window, text=f"{sender}",
                     font=("Roboto", 24, "bold"), text_color="white").pack()

        ctk.CTkLabel(call_window, text="Sizi arıyor.",
                     font=("Arial", 14), text_color="#aaaaaa").pack(pady=(5, 40))

        # Butonlar (Yan Yana)
        btn_frame = ctk.CTkFrame(call_window, fg_color="transparent")
        btn_frame.pack(pady=20)

        def accept():
            # Pencere kapanmışsa işlem yapma
            if not call_window.winfo_exists(): return

            self.send_call_signal("CALL_ACCEPT", sender)
            self.open_private_chat(sender)
            if sender in self.private_chat_windows:
                self.private_chat_windows[sender].set_call_ui_to_active()
            call_window.destroy()  # [cite: 226]

        def reject():
            if not call_window.winfo_exists(): return

            self.send_call_signal("CALL_REJECT", sender)
            call_window.destroy()

        # Reddet Butonu (Kırmızı)
        btn_reject = ctk.CTkButton(btn_frame, text="REDDET", width=100, height=45,
                                   fg_color="#c0392b", hover_color="#e74c3c", corner_radius=20,
                                   command=reject)
        btn_reject.pack(side="left", padx=10)

        # Kabul Et Butonu (Yeşil)
        btn_accept = ctk.CTkButton(btn_frame, text="CEVAPLA", width=100, height=45,
                                   fg_color="#27ae60", hover_color="#2ecc71", corner_radius=20,
                                   command=accept)
        btn_accept.pack(side="left", padx=10)

    def transition_to_chat(self, payload):
        """'Tek Dev Paket'i (payload) alır ve sohbet arayüzünü kurar."""
        try:
            username = payload.get("username")
            history_messages = payload.get("history", [])
            user_list = payload.get("user_list", [])

            self.nickname = username
            self.authenticated = True

            self.clear_widgets()
            self.geometry("650x550")
            self.title(f"Şifreli Chat - {self.nickname} (WebSocket)")
            self.create_chat_ui()  # Önce boş arayüzü kur

            # Sonra arayüzü doldur
            self.load_history_messages(history_messages)
            self.update_online_list_ui(user_list)

        except Exception as e:
            print(f"HATA: Giriş verisi (payload) işlenemedi: {e}")
            self.go_back_to_login("Giriş verisi işlenirken hata oluştu.")

    def e2ee_info_for_peer(self, peer_username: str) -> bytes:
        # Bind HKDF 'info' to stable identities
        a, b = sorted([self.nickname, peer_username])

        return f"chat-e2ee-v1:{a}:{b}".encode("utf-8")

    def start_e2ee_handshake_with(self, peer_username: str):
        # generate ephemeral pair
        from crypto_e2ee import gen_keypair
        my_priv, my_pub = gen_keypair()
        salt = os.urandom(16)
        self.e2ee_sessions[peer_username] = {"my_priv": my_priv, "my_pub": my_pub, "salt": salt}
        payload = {
            "target": peer_username,
            "pub": base64.b64encode(my_pub).decode("utf-8"),
            "salt": base64.b64encode(salt).decode("utf-8"),
        }
        self.run_coroutine_threadsafe(self.send_json_to_server({"command": "KEY_INIT", "payload": payload}))

    def complete_e2ee_handshake(self, peer_username: str, peer_pub_b64: str, salt_b64: str):
        from crypto_e2ee import pubkey_from_bytes, derive_aes_key
        sess = self.e2ee_sessions.get(peer_username)
        peer_pub = pubkey_from_bytes(base64.b64decode(peer_pub_b64))
        salt = base64.b64decode(salt_b64)
        shared = sess["my_priv"].exchange(peer_pub)
        key = derive_aes_key(shared, salt, self.e2ee_info_for_peer(peer_username))
        sess.update({"peer_pub": peer_pub, "aes_key": key, "salt": salt})
        # send back our pub to finalize (if we are responder)
        my_pub_b64 = base64.b64encode(sess["my_pub"]).decode("utf-8")
        reply = {"target": peer_username, "pub": my_pub_b64, "salt": base64.b64encode(salt).decode("utf-8")}
        self.run_coroutine_threadsafe(self.send_json_to_server({"command": "KEY_REPLY", "payload": reply}))
        self.add_message_to_chatbox("SYS_MSG", f"🔐 {peer_username} ile E2E kuruldu.")

    def create_chat_ui(self):
        """
        Görünümü modernize eden yeni arayüz yapısı.
        Sol: Kişi Listesi (Koyu Gri) | Sağ: Sohbet Alanı (Daha Açık)
        """
        # --- Ana Pencere Ayarları ---
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)  # Sol Panel (Daha dar)
        self.grid_columnconfigure(1, weight=4)  # Sağ Panel (Geniş)

        # --- 1. SOL PANEL (Sidebar / Kişi Listesi) ---
        self.sidebar_frame = ctk.CTkFrame(self, fg_color="#212121", corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(1, weight=1)  # Liste alanı esnesin

        # Sol Üst Başlık (Profiliniz)
        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text=f"👤 {self.nickname}",
                                       font=ctk.CTkFont(size=20, weight="bold"),
                                       text_color="#ffffff")
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="nw")

        # Çevrimiçi Kullanıcı Listesi (Scrollable)
        self.online_users_frame = ctk.CTkScrollableFrame(self.sidebar_frame, fg_color="transparent")
        self.online_users_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        # Alt Kısım (Çıkış Butonu vb.)
        self.quit_button = ctk.CTkButton(self.sidebar_frame, text="Çıkış Yap",
                                         fg_color="#C0392B", hover_color="#E74C3C",
                                         command=self.on_closing)
        self.quit_button.grid(row=2, column=0, padx=20, pady=20, sticky="ew")

        # --- 2. SAĞ PANEL (Chat Alanı) ---
        self.main_chat_frame = ctk.CTkFrame(self, fg_color="#2b2b2b", corner_radius=0)  # Arkaplan rengi
        self.main_chat_frame.grid(row=0, column=1, sticky="nsew")

        # Izgara (3 satır: Header, Chat, Input)
        self.main_chat_frame.grid_rowconfigure(0, weight=0)  # Yazıyor... labelı için rezerv (isteğe bağlı header)
        self.main_chat_frame.grid_rowconfigure(1, weight=1)  # Sohbet (En çok bu genişleyecek)
        self.main_chat_frame.grid_rowconfigure(2, weight=0)  # Input alanı
        self.main_chat_frame.grid_columnconfigure(0, weight=1)

        # "Yazıyor..." Etiketi (En üstte dursun)
        self.typing_status_label = ctk.CTkLabel(self.main_chat_frame, text="", height=20,
                                                text_color="#3B8ED0", anchor="w", font=("Arial", 12, "italic"))
        self.typing_status_label.grid(row=0, column=0, sticky="ew", padx=20, pady=(5, 0))

        # Sohbet Kutusu
        self.chat_box = ctk.CTkScrollableFrame(self.main_chat_frame, fg_color="transparent")
        self.chat_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(5, 10))
        self.chat_box.grid_columnconfigure(0, weight=1)

        # --- 3. GİRİŞ ALANI (Input Area) ---
        # Giriş alanını bir "Container" içine alıp yuvarlatılmış ve modern göstereceğiz
        self.input_container = ctk.CTkFrame(self.main_chat_frame, fg_color="#383838", corner_radius=20)
        self.input_container.grid(row=2, column=0, sticky="ew", padx=20, pady=20)
        self.input_container.grid_columnconfigure(0, weight=1)  # Entry genişlesin

        # Mesaj Girişi
        self.message_entry = ctk.CTkEntry(self.input_container,
                                          placeholder_text="Bir mesaj yazın...",
                                          border_width=0, fg_color="transparent", height=40)
        self.message_entry.grid(row=0, column=0, sticky="ew", padx=10)

        # Butonlar (Ses, Kamera, Gönder)
        # Butonları biraz küçültüp zarif hale getirelim
        self.record_button = ctk.CTkButton(self.input_container, text="🎤", width=35, height=35,
                                           fg_color="#444444", hover_color="#555555", corner_radius=15,
                                           command=self.toggle_voice_message)
        self.record_button.grid(row=0, column=1, padx=2)

        self.camera_test_button = ctk.CTkButton(self.input_container, text="📷", width=35, height=35,
                                                fg_color="#444444", hover_color="#555555", corner_radius=15,
                                                command=self.start_camera_preview_window)
        self.camera_test_button.grid(row=0, column=2, padx=2)

        self.send_button = ctk.CTkButton(self.input_container, text="➤", width=40, height=35,
                                         fg_color="#3B8ED0", hover_color="#2678B8", corner_radius=15,
                                         command=self.send_chat_message)
        self.send_button.grid(row=0, column=3, padx=(2, 10))

        # Tuş Bağlantıları
        self.message_entry.bind("<Return>", self.send_chat_message)
        self.message_entry.bind("<KeyRelease>", self.on_key_press)

    async def preview_camera(self):
        cap = cv2.VideoCapture(0)
        while True:
            ret, frame = cap.read()
            if not ret:
                continue
            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img)
            tk_img = CTkImage(light_image=pil_img, size=(320, 240))
            self.video_label.configure(image=tk_img)
            self.video_label.image = tk_img
            await asyncio.sleep(0.03)  # ~30 FPS

    def on_key_press(self, event=None):
        """Kullanıcı mesaj kutusuna bir tuşa bastığında tetiklenir."""

        # 'Enter' tuşuna basıldıysa (bu send_chat_message'in işi) veya
        # '/quit' yazdıysak, 'START' komutu göndermeye gerek yok.
        if event and (event.keysym == 'Return' or self.message_entry.get().startswith('/')):
            return

        # 1. Eğer "Yazmıyor" durumundaysak, "Yazıyor" durumuna geç
        if not self._am_i_typing:
            self.run_coroutine_threadsafe(
                self.send_json_to_server({"command": "TYPING_START", "payload": {}})
            )
            self._am_i_typing = True

        # 2. Mevcut "Durdur" zamanlayıcısı varsa iptal et
        if self._typing_timer:
            self.after_cancel(self._typing_timer)

        # 3. "Durdur" komutunu göndermek için 3 saniyelik YENİ bir zamanlayıcı başlat
        self._typing_timer = self.after(3000, self.stop_typing_action)

    def stop_typing_action(self):
        """Sunucuya 'TYPING_STOP' gönderir ve durumu sıfırlar."""

        # 4. Zamanlayıcıyı sıfırla
        self._typing_timer = None

        # 5. Eğer "Yazıyor" durumundaysak, "Durdur" komutu gönder
        if self._am_i_typing:
            self.run_coroutine_threadsafe(
                self.send_json_to_server({"command": "TYPING_STOP", "payload": {}})
            )
            self._am_i_typing = False

    def update_typing_status(self, username, is_typing):
        """'Kimler yazıyor' listesini ve GUI etiketini günceller."""

        if is_typing:
            self.who_is_typing.add(username)  # Set'e ekle
        else:
            self.who_is_typing.discard(username)  # Set'ten çıkar

        # Arayüz etiketini güncelle
        label_text = ""
        typing_list = list(self.who_is_typing)  # Set'i listeye çevir

        if len(typing_list) == 1:
            label_text = f"{typing_list[0]} yazıyor..."
        elif len(typing_list) == 2:
            label_text = f"{typing_list[0]} ve {typing_list[1]} yazıyor..."
        elif len(typing_list) > 2:
            label_text = "Birkaç kişi yazıyor..."

        self.typing_status_label.configure(text=label_text)
    def load_history_messages(self, history_list):
            """Gelen sohbet geçmişi LİSTESİNİ sohbet kutusuna yükler."""
            try:
                # Geçmişin başına bir ayraç ekle
                self.add_message_to_chatbox("SYS_MSG", "--- Sohbet Geçmiși Yüklendi ---")

                # Gelen tüm geçmiş mesajlar için 'CHAT' komutunu taklit et
                # (Çünkü sunucu [Tarih - Kullanıcı]: Mesaj formatında gönderiyor,
                # bu da 'add_message_to_chatbox'un 'CHAT' parsing'i ile uyumlu)
                for msg in history_list:
                    if msg:  # Boş satırları atla
                        self.add_message_to_chatbox("CHAT", msg)

            except Exception as e:
                print(f"Sohbet geçmişi arayüze yüklenemedi: {e}")

            # --- YENİ v4.0 SESLİ MESAJ FONKSİYONLARI ---

    def toggle_voice_message(self):
        """'Sesli Mesaj' 🎤 butonuna basıldığında tetiklenir."""
        if self.is_recording:
            # 1. Kayıt Zaten Sürüyorsa: Kaydı Durdur
            self.is_recording = False
            self.record_button.configure(text="İşleniyor...", fg_color="#E67E22", state="disabled")
            # Kayıt thread'i 'self.is_recording = False' gördüğünde
            # otomatik olarak duracak ve 'process_and_upload_audio'yu tetikleyecek.
        else:
            # 2. Kaydı Başlat
            self.is_recording = True
            self.audio_frames = []  # Önceki kaydı temizle
            self.record_button.configure(text="🔴 Kayıt (Durdur)", fg_color="red",state="enabled")

            # Kaydı GUI'yi dondurmamak için ayrı bir 'daemon' thread'de başlat
            threading.Thread(target=self._record_audio_worker, daemon=True).start()



    def request_audio_file(self, file_id):
            """Sunucudan indirilmesi için bir ses dosyası talep eder."""
            print(f"DEBUG: Ses dosyası isteniyor: {file_id}")
            payload = {
                "command": "FETCH_AUDIO",
                "payload": {
                    "file_id": file_id
                }
            }
            self.run_coroutine_threadsafe(self.send_json_to_server(payload))



    def play_audio_chunk(self, audio_data_bytes):
        """Sunucudan gelen tam (sıkıştırılmış) ses dosyasını çözer ve çalar."""

        print("DEBUG (Player): Faz 1 - 'play_audio_chunk' tetiklendi.")

        # Sesi çalmak, ana arayüzü (GUI) dondurur.
        # Bu yüzden, sesi 'daemon' bir thread'de açıp çalmalıyız.
        def play_in_thread(audio_bytes):
            try:
                print("DEBUG (Player): Faz 2 (Thread) - Veri 'in-memory' dosyaya yükleniyor...")

                # --- DÜZELTME BURADA BAŞLIYOR ---
                # 1. Ham byte verisini 'dosya gibi' davranan bir hafıza objesine yükle

                audio_file = io.BytesIO(audio_bytes)

                print("DEBUG (Player): Faz 3 (Thread) - 'pydub' (ffmpeg) ile ses çözülüyor...")
                # 2. 'AudioSegment' yerine 'from_file' kullan
                #    ve 'format'ı burada belirt
                segment = pydub.AudioSegment.from_file(audio_file, format="mp3")
                # --- DÜZELTME SONU ---

                print(f"DEBUG (Player): Faz 4 (Thread) - Ses çözüldü! (Süre: {segment.duration_seconds:.1f}s)")

                # 3. 'sounddevice' ile çal
                sd.play(segment.get_array_of_samples(), segment.frame_rate)
                sd.wait()  # Çalma işlemi bitene kadar bekle
                print("DEBUG (Player): Faz 5 (Thread) - Oynatma bitti.")

            except Exception as e:
                print(f"--- SES ÇALMA THREAD HATASI ---")
                print(f"Hata: {e}")
                traceback.print_exc(file=sys.stderr)
                print(f"---------------------------------")
                self.schedule_gui_update(self.add_message_to_chatbox, "SYS_MSG_ERR", f"Ses dosyası oynatılamadı: {e}",
                                         None)

        # 'play_in_thread' fonksiyonunu yeni bir thread'de başlat
        print("DEBUG (Player): Oynatma için yeni thread başlatılıyor...")
        threading.Thread(target=play_in_thread, args=(audio_data_bytes,), daemon=True).start()

    def _record_audio_worker(self):
        """(Worker Thread) 'sounddevice' kullanarak sesi 'self.audio_frames' listesine kaydeder."""

        try:
            # 1. Kaydı başlat
            with sd.InputStream(samplerate=self.rate,
                                blocksize=self.chunk,
                                dtype=self.dtype,
                                channels=self.channels) as stream:

                # Maksimum 10 saniyelik kare (frame) sayısını hesapla
                max_frames = int((self.rate / self.chunk) * self.MAX_RECORD_SECONDS)

                for _ in range(max_frames):
                    # 2. Eğer kullanıcı butona tekrar basıp kaydı durdurduysa (is_recording=False)
                    # veya 10 saniye dolduysa, döngüden çık
                    if not self.is_recording:
                        break

                    data, overflowed = stream.read(self.chunk)
                    self.audio_frames.append(data)

            # 3. Kayıt bitti (ya 10sn doldu ya da kullanıcı durdurdu)
            print(f"Kayıt tamamlandı. {len(self.audio_frames)} parça yakalandı.")
            self.is_recording = False  # Durumu her ihtimale karşı sıfırla

            # 4. Sıkıştırma ve Yükleme işlemini 'asyncio' thread'ine devret
            # ('to_thread' kullanamayız, çünkü bu 'asyncio' thread'i değil,
            # 'threading' thread'i. O yüzden 'run_coroutine_threadsafe' kullanıyoruz)
            self.run_coroutine_threadsafe(self.process_and_upload_audio())


        except Exception as e:

            print(f"Mikrofon kayıt hatası: {e}")

            self.schedule_gui_update(self.add_message_to_chatbox, "SYS_MSG_ERR", f"Mikrofon hatası: {e}")

            # --- DÜZELTİLMİŞ SATIR ---

            self.schedule_gui_update(self.record_button.configure, text="🎤", fg_color="#3B8ED0", state="normal")

    async def process_and_upload_audio(self):
        """(Asyncio Thread) Sesi işler. Özel pencere varsa E2EE, yoksa Genel Chat'e atar."""

        print("DEBUG (Audio): Faz 1 - Ses işleme başladı.")


        try:
            # 1. Ses verisi ve Bağlantı Kontrolü
            if not self.audio_frames:
                print("DEBUG (Audio): Ses verisi boş.")
                return

            if self.websocket is None:
                raise Exception("Sunucu bağlantısı yok! Lütfen tekrar giriş yapın.")

            # 2. HEDEF BELİRLEME (Genel mi, Özel mi?)
            # Eğer özel pencere açıksa, en son açılanı hedef al (Özel Mesaj)
            # Eğer hiç pencere yoksa, hedef None olur (Genel Sohbet)
            target_user = list(self.private_chat_windows.keys())[-1] if self.private_chat_windows else None

            # 3. Sıkıştırma (Ortak İşlem)
            recording_data = np.concatenate(self.audio_frames)

            def convert_to_mp3_bytes(data, rate, channels):
                import io
                import pydub
                segment = pydub.AudioSegment(
                    data=data.tobytes(),
                    sample_width=data.dtype.itemsize,
                    frame_rate=rate,
                    channels=channels
                )
                mp3_io = io.BytesIO()
                segment.export(mp3_io, format="mp3", bitrate="64k")
                return mp3_io.getvalue(), segment.duration_seconds

            audio_bytes, duration = await asyncio.to_thread(
                convert_to_mp3_bytes, recording_data, self.rate, self.channels
            )
            print(f"DEBUG (Audio): Sıkıştırma bitti. Süre: {duration:.1f}s")

            # ---------------------------------------------------------
            # SENARYO A: ÖZEL MESAJ (E2EE ŞİFRELİ)
            # ---------------------------------------------------------
            if target_user:
                print(f"DEBUG (Audio): Hedef: {target_user} (Özel Şifreli)")

                sess = self.e2ee_sessions.get(target_user)
                if not sess or "aes_key" not in sess:
                    raise Exception(f"{target_user} ile şifreli oturum yok. Önce mesajlaşın.")

                from crypto_e2ee import seal
                import base64

                aad = f"[{self.nickname}->{target_user}:AUDIO]".encode("utf-8")
                nonce, ciphertext = seal(sess["aes_key"], audio_bytes, aad=aad)

                payload = {
                    "command": "ENC_AUDIO_MSG",
                    "payload": {
                        "target": target_user,
                        "duration_seconds": duration,
                        "nonce": base64.b64encode(nonce).decode("utf-8"),
                        "salt": base64.b64encode(sess["salt"]).decode("utf-8"),
                        "ct": base64.b64encode(ciphertext).decode("utf-8"),
                        "aad": base64.b64encode(aad).decode("utf-8")
                    }
                }
                await self.send_json_to_server(payload)
                self.schedule_gui_update(self.add_message_to_chatbox, "DM", f"[Siz -> {target_user}]: 🎤 (Şifreli Ses)",
                                         None)

            # ---------------------------------------------------------
            # SENARYO B: GENEL SOHBET (STANDART)
            # ---------------------------------------------------------
            else:
                print("DEBUG (Audio): Hedef: GENEL SOHBET (Public)")
                import base64

                # Genel sohbet için şifreleme yok, direkt base64
                audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')

                payload = {
                    "command": "AUDIO_MSG",
                    "payload": {
                        "filedata_b64": audio_base64,
                        "format": "mp3",
                        "duration_seconds": duration
                    }
                }

                await self.send_json_to_server(payload)
                self.schedule_gui_update(self.add_message_to_chatbox, "chat", f"[Siz]: 🎤 Sesli Mesaj Gönderildi", None)

            print("DEBUG (Audio): Gönderim başarılı.")

        except Exception as e:
            print(f"Genel Ses Hatası: {e}")
            import traceback
            traceback.print_exc()
            self.schedule_gui_update(self.add_message_to_chatbox, "SYS_MSG_ERR", f"Ses gönderilemedi: {e}", None)

        finally:
            # Butonu sıfırla
            def reset_button():
                try:
                    self.record_button.configure(text="🎤", fg_color="#444444", state="normal")
                except:
                    pass

            self.schedule_gui_update(reset_button)

    def update_online_list_ui(self, user_list):
        """Kişi listesini modern kartlar şeklinde günceller."""
        try:
            for widget in self.online_users_frame.winfo_children():
                widget.destroy()

            # Başlık
            ctk.CTkLabel(self.online_users_frame, text="ÇEVRİMİÇİ KULLANICILAR",
                         font=("Arial", 10, "bold"), text_color="#777777", anchor="w").grid(row=0, column=0,
                                                                                            sticky="ew", padx=10,
                                                                                            pady=(0, 10))

            row_index = 1
            for user_data in user_list:
                username = user_data.get("username", "Bilinmeyen")
                role = user_data.get("role", "user")

                # Admin kontrolü
                icon_text = "👤"
                name_color = "#dddddd"
                if role == 'admin':
                    icon_text = "🛡️"
                    name_color = "#ffb74d"  # Admin için turuncu ton

                # Kart Tasarımı (Buton olarak)
                display_text = f"  {icon_text}  {username}"

                user_btn = ctk.CTkButton(self.online_users_frame,
                                         text=display_text,
                                         anchor="w",
                                         fg_color="transparent",
                                         text_color=name_color,
                                         hover_color="#333333",  # Üzerine gelince hafif aydınlanma
                                         corner_radius=8,
                                         height=35,
                                         font=("Arial", 13),
                                         command=lambda u=username: self.open_private_chat(u))

                user_btn.grid(row=row_index, column=0, sticky="ew", padx=5, pady=2)

                if username == self.nickname:
                    user_btn.configure(state="disabled", text=f"  🟢  {username} (Sen)", text_color="#2ecc71")

                row_index += 1
        except Exception as e:
            pass


    def send_chat_message(self, event=None):
            """Mesajı veya komutu JSON formatında sunucuya gönderir."""

            # 1. Her zaman "yazmayı durdur" komutunu tetikle
            self.stop_typing_action()

            message = self.message_entry.get()
            if not message:
                return

            payload_json = None  # Gönderilecek bir şey var mı diye kontrol için None ile başla

            # --- Komut Zinciri Başlangıcı ---

            # 1. Çıkış Komutları
            if message.lower() == '/quit' or message.lower() == '/exit':
                self.on_closing()
                return  # Fonksiyondan tamamen çık

            # 2. Yardım Komutu (Yerel)
            elif message.lower() == '/help':
                self.add_message_to_chatbox("SYS_MSG", "--- Komut Listesi ---")
                self.add_message_to_chatbox("SYS_MSG", " /dm <kullanici> <mesaj> - Özel mesaj gönderir.")
                self.add_message_to_chatbox("SYS_MSG", " /kick <kullanici> (Admin yetkisi gerekir)")
                self.add_message_to_chatbox("SYS_MSG", " /quit veya /exit - Sohbetten çıkar.")
                self.add_message_to_chatbox("SYS_MSG", " /help - Bu yardım menüsünü gösterir.")
                self.message_entry.delete(0, "end")
                return  # Fonksiyondan tamamen çık

            # 3. DM Komutu (Sunucuya Gönder)
            elif message.startswith('/dm '):
                parts = message.split(' ', 2)
                if len(parts) < 3:
                    self.add_message_to_chatbox("SYS_MSG_ERR", "Kullanım: /dm <kullanici> <mesaj>")
                    self.message_entry.delete(0, "end")
                    return  # Hatalı, fonksiyondan çık

                payload_json = {"command": "DM", "payload": {"target": parts[1], "message": parts[2]}}

            # 4. Kick Komutu (Sunucuya Gönder)
            elif message.startswith('/kick '):
                parts = message.split(' ', 1)
                if len(parts) < 2 or ' ' in parts[1] or not parts[1]:
                    self.add_message_to_chatbox("SYS_MSG_ERR", "Kullanım: /kick <kullanici_adi>")
                    self.message_entry.delete(0, "end")
                    return  # Hatalı, fonksiyondan çık

                target_user = parts[1]
                payload_json = {"command": "KICK", "payload": {"target": target_user}}

            # 5. Normal Sohbet Mesajı (Sunucuya Gönder)
            else:
                payload_json = {"command": "CHAT", "payload": {"message": message}}

            # --- Komut Zinciri Sonu ---

            # Eğer gönderilecek geçerli bir 'payload' varsa (yani /help veya /quit değilse)
            if payload_json:
                self.run_coroutine_threadsafe(self.send_json_to_server(payload_json))
                self.play_outgoing_sound()

            self.message_entry.delete(0, "end")

    def add_message_to_chatbox(self, command, payload, sender=None):
        """Mesajları baloncuk içine koyar. Ses mesajı metni görürse BUTON oluşturur."""

        # --- Renk Ayarları ---
        colors = {
            "own_bg": "#005c4b", "own_text": "#e9edef",
            "other_bg": "#363636", "other_text": "#e9edef",
            "system": "transparent", "system_text": "#888888",
            "error_text": "#ff6b6b"
        }

        # Varsayılanlar
        bubble_color = colors["other_bg"]
        text_color = colors["other_text"]
        sticky_side = "w"  # Sola yaslı
        message_type = "other"

        # Payload string'e çevrilir (garanti olsun)
        payload_str = str(payload)

        # --- SES MESAJI KONTROLÜ (Sizin Formatınız: [▶️ Sesli Mesaj - ID: 123]) ---
        is_audio = False
        audio_id = None

        if "[▶️ Sesli Mesaj" in payload_str and "ID:" in payload_str:
            try:
                # ID'yi çekip alıyoruz
                # Örnek metin: "[Ahmet]: [▶️ Sesli Mesaj - ID: 55]" -> 55'i alır
                audio_id = payload_str.split("ID:")[1].strip().split("]")[0].strip()
                is_audio = True
            except:
                is_audio = False

        # --- Mesajın Yönünü Belirle (Sağ mı Sol mu?) ---
        if command == "DM" and payload_str.startswith("[Siz ->"):
            message_type = "own"
        elif command == "CHAT":
            # Mesajın başındaki "Username]:" kısmını kontrol et
            if sender == self.nickname:
                message_type = "own"
            elif payload_str.startswith(f"[{self.nickname}]:") or payload_str.startswith(f"{self.nickname}:"):
                message_type = "own"
        elif command == "SYS_MSG":
            message_type = "system"
            text_color = colors["system_text"]
        elif command == "SYS_MSG_ERR":
            message_type = "system"
            text_color = colors["error_text"]

        if message_type == "own":
            bubble_color = colors["own_bg"]
            sticky_side = "e"  # Sağa yaslı

        try:
            # 1. Baloncuk Çerçevesi
            bubble_wrapper = ctk.CTkFrame(self.chat_box, fg_color="transparent")
            bubble_wrapper.pack(anchor=sticky_side, padx=10, pady=5, fill="x")

            # 2. İÇERİK: Buton mu Yazı mı?
            if is_audio and audio_id:
                # --- OYNAT BUTONU ---
                # Butona basılınca 'request_audio_file' çalışacak
                # O da sunucudan sesi isteyip 'play_audio_chunk' ile çalacak.
                btn_cmd = lambda: self.request_audio_file(audio_id)

                btn = ctk.CTkButton(bubble_wrapper, text=f"▶ Sesli Mesaj (Çal)",
                                    fg_color=bubble_color,
                                    text_color=text_color,
                                    hover_color="#202c33",
                                    width=140, height=35,
                                    corner_radius=15,
                                    command=btn_cmd)
                btn.pack(anchor=sticky_side)

            else:
                # --- NORMAL YAZI ---
                msg_widget = ctk.CTkLabel(bubble_wrapper, text=payload_str,
                                          fg_color=bubble_color, text_color=text_color,
                                          corner_radius=16, wraplength=350, justify="left",
                                          padx=12, pady=8, font=("Roboto", 13))
                msg_widget.pack(anchor=sticky_side)

            # En alta kaydır
            self.after(50, lambda: self.chat_box._parent_canvas.yview_moveto(1.0))

        except Exception as e:
            print(f"Baloncuk hatası: {e}")

    def set_auth_buttons_state(self, state):
            """Giriş ve Kayıt butonlarının durumunu ayarlar ('normal' veya 'disable')."""
            try:
                if state == "disable":
                    if hasattr(self, 'login_button'):  # Butonun varlığını kontrol et
                        self.login_button.configure(state=state)
                    if hasattr(self, 'register_button'):
                        self.register_button.configure(state="normal")
                else:
                    if hasattr(self, 'login_button'):
                        self.login_button.configure(state="normal")
                    if hasattr(self, 'register_button'):
                        self.register_button.configure(state="normal")
            except (AttributeError, tkinter.TclError):
                # Butonlar henüz oluşturulmadıysa (nadiren olur) görmezden gel
                pass

    def clear_widgets(self):
        """Penceredeki tüm bileşenleri (widget) temizler."""
        # .grid() ile yerleştirilen widget'ları temizlemenin en iyi yolu
        # .winfo_children() kullanmaktır, ancak ana pencere ızgarasını da sıfırlamalıyız

        # Önce tüm alt widget'ları yok et
        for widget in self.winfo_children():
            widget.destroy()

        # Ana pencerenin ızgara yapılandırmasını sıfırla
        # (Bu, yeni 'create' fonksiyonunun kendi ızgarasını kurabilmesi için önemlidir)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=0)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=0)
        self.grid_columnconfigure(2, weight=0)

    def go_back_to_login(self, error_message):
        """Bağlantı koptuğunda arayüzü sohbetten girişe döndürür."""
        if not self.authenticated:
            # Zaten giriş ekranındayken bağlantı koptuysa...
            self.show_auth_error(error_message)
            return

        # Sohbet ekranındayken bağlantı koptuysa...
        self.authenticated = False
        self.nickname = ""
        self.create_auth_ui()  # Giriş arayüzünü yeniden kur
        self.show_auth_error(error_message)  # Ve hatayı göster

    def play_incoming_sound(self):
        """Mevcut bir *gelen* ses zamanlayıcısı varsa iptal eder ve yenisini başlatır."""
        if self._sound_cooldown_timer_in:
            self.after_cancel(self._sound_cooldown_timer_in)
        # Düzeltme burada (tek 'actually' ve fonksiyon adının başındaki '_' (alt tire)):
        self._sound_cooldown_timer_in = self.after(300, self._actually_play_incoming)
    def _actually_play_incoming(self):
            """Zamanlayıcı bittiğinde *gelen* sesi çalar."""
            try:

                winsound.PlaySound(resource_path("assets/message.wav"), winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception as e:
                pass
            finally:
                self._sound_cooldown_timer_in = None

    def play_outgoing_sound(self):
        """Mevcut bir *giden* ses zamanlayıcısı varsa iptal eder ve yenisini başlatır."""
        if self._sound_cooldown_timer_out:
            self.after_cancel(self._sound_cooldown_timer_out)
        self._sound_cooldown_timer_out = self.after(300, self._actually_play_outgoing)

    def _actually_play_outgoing(self):
        """Zamanlayıcı bittiğinde *giden* sesi çalar."""
        try:
            # Giden ses dosyasının 'assets' klasöründe olduğunu varsayıyorum
            winsound.PlaySound(resource_path("assets/message.wav"), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            print(f"Giden ses dosyası ('assets/message.wav') bulunamadı: {e}")
            pass
        finally:
            self._sound_cooldown_timer_out = None




    def start_camera_preview_window(self):
        """Kamera testi için yeni bir pencere açar."""

        # Zaten bir test penceresi açık mı?
        if hasattr(self, "camera_preview_window") and self.camera_preview_window.winfo_exists():
            self.camera_preview_window.lift()  # Pencereyi öne getir
            return

        # Yeni Toplevel penceresi oluştur
        self.camera_preview_window = ctk.CTkToplevel(self)
        self.camera_preview_window.title("Kamera Testi (Lokal Önizleme)")
        self.camera_preview_window.geometry("640x480")

        # Video görüntüsünün gösterileceği etiketi oluştur
        self.camera_preview_label = ctk.CTkLabel(self.camera_preview_window, text="Kamera bağlanıyor...")
        self.camera_preview_label.pack(fill="both", expand=True)

        # Kamera akışını (coroutine) güvenli bir şekilde başlat
        self.camera_preview_task = self.run_coroutine_threadsafe(
            self.run_local_camera_feed(self.camera_preview_label)
        )

        # Pencere kapatıldığında coroutine'i durdurmak için protokol ata
        self.camera_preview_window.protocol(
            "WM_DELETE_WINDOW", self.stop_camera_preview_window
        )

    def stop_camera_preview_window(self):
        """Kamera test penceresini ve kamera akışını güvenle durdurur."""

        # 1. Arka planda çalışan kamera coroutine'ini iptal et
        if hasattr(self, "camera_preview_task"):
            try:
                # 'run_coroutine_threadsafe' bir 'future' nesnesi döndürür
                # Bu 'future' üzerinden 'cancel()' çağrılabilir
                self.camera_preview_task.cancel()
            except Exception as e:
                print(f"Kamera görevini iptal etme hatası: {e}")

        # 2. Pencereyi yok et
        if hasattr(self, "camera_preview_window") and self.camera_preview_window.winfo_exists():
            self.camera_preview_window.destroy()

        # 3. Referansları temizle
        if hasattr(self, "camera_preview_window"):
            del self.camera_preview_window
        if hasattr(self, "camera_preview_label"):
            del self.camera_preview_label
        if hasattr(self, "camera_preview_task"):
            del self.camera_preview_task

    async def run_local_camera_feed(self, video_label):
        """Lokal kamerayı açar ve sağlanan CTkLabel'a yansıtır (Hata Korumalı)."""
        cap = None
        try:
            # Kamerayı başlat (DSHOW Windows için daha hızlı açılmasını sağlar)
            cap = cv2.VideoCapture(0 + cv2.CAP_DSHOW)

            if not cap.isOpened():
                print("HATA: Kamera (index 0) açılamadı!")
                self.schedule_gui_update(video_label.configure, text="Hata: Kamera açılamadı.")
                return

            while True:
                # 1. Kareyi Yakala
                ret, frame = cap.read()
                if not ret:
                    break

                # 2. Görüntüyü İşle (BGR -> RGB)
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(img)

                # --- KRİTİK DÜZELTME BAŞLANGICI ---
                # Pencere boyutunu alırken pencere kapanmış olabilir.
                # Bu yüzden bu işlemi try-except içine alıyoruz.
                try:
                    if not video_label.winfo_exists():
                        break  # Pencere yoksa döngüyü kır

                    w = video_label.winfo_width()
                    h = video_label.winfo_height()

                    # Pencere henüz çizilmediyse veya küçüldüyse (ikon durumundaysa) koruma
                    if w < 10 or h < 10:
                        await asyncio.sleep(0.1)
                        continue

                except Exception:
                    # "bad window path name" hatası burada yakalanır ve döngü güvenle biter.
                    break
                # --- KRİTİK DÜZELTME SONU ---

                # 3. Yeniden Boyutlandır (Thumbnail daha performanslıdır)
                pil_img.thumbnail((w, h), Image.LANCZOS)
                tk_img = CTkImage(light_image=pil_img, size=pil_img.size)

                # 4. GUI Güncelleme
                def update_gui_label(img_to_set=tk_img):
                    try:
                        if video_label.winfo_exists():
                            video_label.configure(image=img_to_set, text="")
                            video_label.image = img_to_set
                    except Exception:
                        pass

                self.schedule_gui_update(update_gui_label)

                # 30 FPS bekleme
                await asyncio.sleep(0.03)

        except asyncio.CancelledError:
            print("Kamera önizlemesi (lokal) durduruldu.")
        except Exception as e:
            print(f"Kamera önizleme hatası: {e}")
        finally:
            if cap:
                cap.release()

            # Temizlik
            def clear_gui_label():
                try:
                    if video_label.winfo_exists():
                        video_label.configure(image=None, text="Kamera Kapatıldı.")
                        video_label.image = None
                except:
                    pass

            self.schedule_gui_update(clear_gui_label)





    async def shutdown_async_tasks(self):
            """Asyncio görevlerini (websocket) güvenle kapatır ve loop'u durdurur."""
            print("DEBUG (Async): Kapatma coroutine'i başladı...")
            try:
                if self.websocket:
                    await self.websocket.close()
                    print("DEBUG (Async): WebSocket kapatıldı.")
            except Exception as e:
                print(f"DEBUG (Async): WebSocket kapatılırken hata: {e}")
            finally:
                print("DEBUG (Async): Event loop durduruluyor.")
                if self.asyncio_loop.is_running():
                    self.asyncio_loop.stop()


    def on_closing(self):
            """Pencere kapatıldığında tetiklenir."""
            print("DEBUG (Main): Kapatma isteği gönderildi...")

            # Hata ayıklama: shutdown_async_tasks'in var olup olmadığını kontrol et
            if not hasattr(self, 'shutdown_async_tasks'):
                print("KRİTİK HATA: shutdown_async_tasks fonksiyonu bulunamadı!")
                self.destroy()  # Kaba kuvvetle kapat
                return

            if self.websocket or self.asyncio_loop.is_running():
                # Arka plan thread'ine 'kendini nazikçe kapat' görevini ver
                self.run_coroutine_threadsafe(self.shutdown_async_tasks())

            # Pencereyi hemen yok et (kullanıcı beklemesin)
            self.destroy()


if __name__ == "__main__":
    app = ChatApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()