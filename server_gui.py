import asyncio
import signal
import base64 # <-- YENİ
import uuid   # <-- YENİ (Dosyaya benzersiz isim vermek için)
import username
import websockets
import asyncpg  # <-- YENİ: sqlite3 yerine
import json
import os
from fractions import Fraction

import datetime as date
import ssl # <-- BU SATIRI EKLE
import sys
import traceback
import platform
from passlib.context import CryptContext
from pydub import AudioSegment

# --- 1. AYARLAR VE SABİTLER ---
HOST = '0.0.0.0'
PORT = 50505

# --- YENİ: PostgreSQL Bağlantı Bilgileri ---
# Faz 1'de oluşturduğunuz kullanıcı adı, şifre ve veritabanı adı
DB_USER = os.getenv("DB_USER", "db_username")
DB_PASS = os.getenv("DB_PASS", "db_password") 
DB_NAME = "chat_app"
DB_HOST = "127.0.0.1" # Yerel sunucunuz (Radore'da da bu olabilir)
# ---

# Güvenlik Sınırları (Aynı)
MAX_USERNAME_LEN = 32
MAX_PASSWORD_LEN = 72
MAX_MESSAGE_LEN = 512
MAX_DM_TARGET_LEN = 32
MAX_AUDIO_SIZE = 1 * 1024 * 1024 # 1 MB ses limiti

try:
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
except Exception as e:
    print(f"KRİTİK HATA: passlib/bcrypt yüklenemedi! Hata: {e}", file=sys.stderr)
    exit()

authenticated_clients = {}

# --- YENİ: Global Veritabanı Havuzu ---
DB_POOL = None


# ---

# --- 2. VERİTABANI VE YETKİLENDİRME FONKSİYONLARI (TAMAMEN YENİLENDİ) ---

async def setup_database():
    """Sunucu başlarken tabloları oluşturur ve 'admin' kullanıcısını tohumlar."""

    # --- YENİ BLOK ---
    # Dosya yüklemeleri için klasör yapısını oluştur
    os.makedirs("uploads/audio", exist_ok=True)
    print("Dosya yükleme ('uploads/audio') klasörü kontrol edildi/oluşturuldu.")
    # --- YENİ BLOK SONU ---
    # Havuzdan bir bağlantı al
    async with DB_POOL.acquire() as conn:
        # PostgreSQL'in syntax'ı (sözdizimi) biraz farklıdır
        # (örn: AUTOINCREMENT yerine SERIAL, DATETIME yerine TIMESTAMPTZ)
        await conn.execute('''
                           CREATE TABLE IF NOT EXISTS users
                           (
                               username
                               TEXT
                               PRIMARY
                               KEY
                               NOT
                               NULL,
                               password_hash
                               TEXT
                               NOT
                               NULL,
                               role
                               TEXT
                               NOT
                               NULL
                               DEFAULT
                               'user'
                           )
                           ''')
        await conn.execute('''
                           CREATE TABLE IF NOT EXISTS public_messages
                           (
                               id
                               SERIAL
                               PRIMARY
                               KEY,
                               sender_username
                               TEXT
                               NOT
                               NULL,
                               message_text
                               TEXT
                               NOT
                               NULL,
                               timestamp
                               TIMESTAMPTZ
                               DEFAULT
                               CURRENT_TIMESTAMP
                           )
                           ''')
        await conn.execute(''' CREATE TABLE IF NOT EXISTS private_messages
                               (
                                   id
                                   SERIAL
                                   PRIMARY
                                   KEY,
                                   sender_username
                                   TEXT
                                   NOT
                                   NULL,
                                   target_username
                                   TEXT
                                   NOT
                                   NULL,
                                   message_text
                                   TEXT
                                   NOT
                                   NULL,
                                   timestamp
                                   TIMESTAMPTZ
                                   DEFAULT
                                   CURRENT_TIMESTAMP
                               )''')

        # Admin Tohumlama
        # ... setup_database fonksiyonunun içi ...
        try:
            admin_user = "admin"
            admin_pass = "123456789Apo54.!"
            hashed_pw = await asyncio.to_thread(hash_password, admin_pass)

            # SQL'i '?' yerine '$1, $2, $3' ile yazdığımıza dikkat et
            # 'ON CONFLICT (username) DO NOTHING' -> 'INSERT OR IGNORE'un Postgres karşılığı
            await conn.execute("""
                               INSERT INTO users (username, password_hash, role)
                               VALUES ($1, $2, $3)
ON CONFLICT (username)
DO UPDATE SET password_hash = EXCLUDED.password_hash;
                               """, admin_user, hashed_pw, 'admin')  # <-- DÜZELTME: Burası 'admin' olmalı

            print(f"Admin kullanıcısı '{admin_user}' kontrol edildi/oluşturuldu.")
        except Exception as e:
            print(f"Admin tohumlama sırasında hata: {e}", file=sys.stderr)
        # ...

    print(f"PostgreSQL Veritabanı '{DB_NAME}' ve tablolar hazır.")


def hash_password(password):
    # Bu CPU-yoğun bir işlem, 'to_thread' gerektirmez, hızlı çalışır
    return pwd_context.hash(password)


def verify_password(password, hashed_password):
    # Bu da CPU-yoğun
    try:
        return pwd_context.verify(password, hashed_password)
    except:
        return False


# 'async def' oldu, 'asyncio.to_thread' kalktı
async def register_user(username, password):
    """Yeni bir kullanıcıyı 'user' rolüyle veritabanına kaydeder."""

    if not username or not password or len(username) > MAX_USERNAME_LEN or len(
            password.encode('utf-8')) > MAX_PASSWORD_LEN:
        return {"command": "AUTH_FAIL", "payload": "Giriş bilgileri geçersiz/çok uzun."}

    try:
        # Havuzdan bir bağlantı "ödünç al"
        async with DB_POOL.acquire() as conn:
            # fetchrow -> tek bir satır getir
            existing_user = await conn.fetchrow("SELECT username FROM users WHERE username = $1", username)
            if existing_user:
                return {"command": "AUTH_FAIL", "payload": "Kullanıcı adı zaten alınmış."}

            new_role = 'user'
            hashed_pw = await asyncio.to_thread(hash_password, password)

            await conn.execute("INSERT INTO users (username, password_hash, role) VALUES ($1, $2, $3)",
                               username, hashed_pw, new_role)

        print(f"Yeni kullanıcı kayıt oldu: {username} (Rol: {new_role})")
        return {"command": "REGISTER_SUCCESS", "payload": "Kayıt başarılı. Şimdi giriş yapabilirsiniz."}

    except Exception as e:
        print(f"REGISTER_USER HATASI: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return {"command": "AUTH_FAIL", "payload": f"Sunucu veritabanı hatası"}


# 'async def' oldu, 'asyncio.to_thread' kalktı
async def check_login(username, password):
    """Kullanıcıyı doğrular ve 'role' bilgisini de döndürür."""

    if not username or not password or len(username) > MAX_USERNAME_LEN:
        return {"command": "AUTH_FAIL", "payload": "Giriş bilgileri geçersiz."}, None, None

    try:
        async with DB_POOL.acquire() as conn:
            result = await conn.fetchrow("SELECT username, password_hash, role FROM users WHERE username = $1", username)

        if not result:
            return {"command": "AUTH_FAIL", "payload": "Kullanıcı bulunamadı."}, None, None

    # asyncpg satırları sözlük gibi döndürür (sütun adıyla erişim harikadır)
        hashed_pw_from_db = result['password_hash']
        if isinstance(hashed_pw_from_db, bytes):  # bytea dönüşünü engelle
            hashed_pw_from_db = hashed_pw_from_db.decode('utf-8')
        user_role = result['role']

        if await asyncio.to_thread(verify_password, password, hashed_pw_from_db):
            return {"command": "AUTH_SUCCESS", "payload": "Giriş başarılı."}, username, user_role
        else:
            return {"command": "AUTH_FAIL", "payload": "Yanlış şifre."}, None, None
    except Exception as e:
        print(f"CHECK_LOGIN HATASI: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return {"command": "AUTH_FAIL", "payload": f"Giriş hatası: {e}"}, None, None


# --- 3. ASENKRON AĞ VE YÖNETİM FONKSİYONLARI ---

async def broadcast(message_json, exclude_websocket=None):
    if authenticated_clients:
        message_str = json.dumps(message_json)
        tasks = []
        for websocket in authenticated_clients.keys():
            if websocket != exclude_websocket:
                # websocket.send'in bir istisna (exception) fırlatması durumunda
                # gather'ın çökmemesi için 'shield' kullanılabilir, ama basit tutalım.
                tasks.append(websocket.send(message_str))

        # return_exceptions=True, bir bağlantı koptuğunda diğerlerinin devam etmesini sağlar
        await asyncio.gather(*tasks, return_exceptions=True)


async def broadcast_user_list(exclude_websocket=None):
    if not authenticated_clients: return
    user_list_payload = list(authenticated_clients.values())
    message_json = {"command": "USER_LIST_UPDATE", "payload": user_list_payload}
    await broadcast(message_json, exclude_websocket=exclude_websocket)


async def handle_dm(sender_username, target_username, message_text):
    target_socket = None;
    sender_socket = None

    try:
        async with DB_POOL.acquire() as conn:
            await conn.execute(
                "INSERT INTO private_messages (sender_username, target_username, message_text) VALUES ($1, $2, $3)",
                sender_username, target_username, message_text
            )
    except Exception as e:
        print(f"DM veritabanına kaydedilemedi: {e}", file=sys.stderr)

    # .items() kopyası üzerinde dönmek, döngü sırasında değişiklik yapmaya izin verir
    for socket, data in list(authenticated_clients.items()):
        if data["username"] == target_username: target_socket = socket
        if data["username"] == sender_username: sender_socket = socket

    dm_to_target = {"command": "DM", "payload": f"[{sender_username} -> Siz]: {message_text}"}
    dm_to_sender = {"command": "DM", "payload": f"[Siz -> {target_username}]: {message_text}"}

    tasks = []
    if target_socket:
        tasks.append(target_socket.send(json.dumps(dm_to_target)))
        if sender_socket: tasks.append(sender_socket.send(json.dumps(dm_to_sender)))
    else:
        if sender_socket:
            error_msg = {"command": "SYS_MSG_ERR", "payload": f"Hata: '{target_username}' kullanıcısı çevrimiçi değil."}
            tasks.append(sender_socket.send(json.dumps(error_msg)))

    if tasks: await asyncio.gather(*tasks, return_exceptions=True)



# Kayıt dizini
AUDIO_SAVE_DIR = "audio_records"
os.makedirs(AUDIO_SAVE_DIR, exist_ok=True)

async def relay_signal(command, payload, current_username, websocket):
    target_username = payload.get("target")
    if not target_username:
        print(f"DEBUG ({current_username}): {command} atlandı (target eksik).")
        return

    target_socket = next((ws for ws, d in authenticated_clients.items()
                          if d.get("username") == target_username), None)

    if not target_socket:
        await websocket.send(json.dumps({
            "command": "SYS_MSG_ERR",
            "payload": f"{target_username} çevrimdışı."
        }))
        return

    relay_payload = dict(payload)
    relay_payload["from"] = current_username

    await target_socket.send(json.dumps({
        "command": command,
        "payload": relay_payload
    }))
    print(f"DEBUG ({current_username}): {command} -> {target_username}")

async def broadcast_audio(audio_chunk, sender_username=None, exclude_websocket=None):
    """
    İkili (binary) ses parçasını herkese yayınlar ve kaydeder.
    """
    # 1️⃣ Dosya kaydı
    if sender_username:
        filename = os.path.join(AUDIO_SAVE_DIR, f"{sender_username}_{int(date.datetime.now().timestamp())}.raw")
        try:
            with open(filename, "ab") as f:
                f.write(audio_chunk)
        except Exception as e:
            print(f"Ses kaydedilemedi: {e}", file=sys.stderr)

    # 2️⃣ Canlı broadcast
    tasks = []
    for websocket in authenticated_clients.keys():
        if websocket != exclude_websocket:
            tasks.append(websocket.send(audio_chunk))
    await asyncio.gather(*tasks, return_exceptions=True)

    # 3️⃣ Chat mesajı olarak broadcast (opsiyonel)
    if sender_username:
        timestamp = date.datetime.now().strftime("%H:%M")
        formatted_message = {
            "command": "CHAT",
            "payload": f"[{timestamp} - {sender_username}]: Ses mesajı gönderildi 🎤"
        }
        await broadcast(formatted_message, exclude_websocket=None)



async def broadcast_audio_status(username, status, exclude_websocket=None):
    """Kullanıcının ses kaydetme durumunu herkese duyurur."""
    if status == "started":
        msg = f"[{username}] ses kaydetmeye başladı."
    else:
        msg = f"[{username}] ses kaydını bitirdi."
    message_json = {"command": "SYS_MSG", "payload": msg}
    await broadcast(message_json, exclude_websocket=exclude_websocket)


async def handle_kick(admin_username, target_username, admin_websocket):
    if admin_username == target_username:
        error_msg = {"command": "SYS_MSG_ERR", "payload": "Kendinizi atamazsınız."};
        await admin_websocket.send(json.dumps(error_msg));
        return

    target_socket = None;
    target_role = None
    for socket, data in authenticated_clients.items():
        if data["username"] == target_username: target_socket = socket; target_role = data.get("role"); break

    if not target_socket:
        error_msg = {"command": "SYS_MSG_ERR", "payload": f"Kullanıcı '{target_username}' bulunamadı."};
        await admin_websocket.send(json.dumps(error_msg));
        return
    if target_role == 'admin':
        error_msg = {"command": "SYS_MSG_ERR", "payload": "Başka bir Admin'i atamazsınız."};
        await admin_websocket.send(json.dumps(error_msg));
        return

    try:
        kick_msg_to_target = {"command": "KICK_SIGNAL",
                              "payload": "Sunucudan bir admin tarafından atıldınız. Giriş ekranına yönlendiriliyorsunuz..."}
        await target_socket.send(json.dumps(kick_msg_to_target))
        success_msg_to_admin = {"command": "SYS_MSG", "payload": f"'{target_username}' kullanıcısı başarıyla atıldı."}

        try:
            await admin_websocket.send(json.dumps(success_msg_to_admin))
        except:
            pass  # Adminin bağlantısı da koptuysa görmezden gel

        await asyncio.sleep(0.05)  # Mesajın gitmesi için zaman tanı
        await target_socket.close(code=1000, reason="Kicked by admin")

    except Exception as e:
        print(f"Kick işlemi sırasında hata: {e}", file=sys.stderr)
        try:
            error_msg = {"command": "SYS_MSG_ERR", "payload": "Kullanıcı atılırken bir hata oluştu."};
            await admin_websocket.send(json.dumps(error_msg))
        except:
            pass


# --- 4. ANA İŞLEYİCİ 'HANDLER' (GÜNCELLENDİ) ---

async def handler(websocket):
    """Her WebSocket bağlantısını yöneten ana asenkron fonksiyon."""

    current_username = None;
    current_role = None
    AUDIO_DIR = "audio_records"
    os.makedirs(AUDIO_DIR, exist_ok=True)
    user_audio_chunks = []  # websocket scope

    try:
        # --- AŞAMA 1: KİMLİK DOĞRULAMA DÖNGÜSÜ ---
        async for message in websocket:
            try:
                data = json.loads(message); command = data.get("command"); payload = data.get("payload", {})
            except json.JSONDecodeError:
                continue

            response_json = {}
            if command == "REGISTER":
                # 'to_thread' GİTTİ, yerine 'await' GELDİ
                response_json = await register_user(payload.get("user"), payload.get("pass"))

            elif command == "LOGIN":
                user = payload.get("user");
                pwd = payload.get("pass")
                if user in [data['username'] for data in authenticated_clients.values()]:
                    response_json = {"command": "AUTH_FAIL", "payload": "Bu kullanıcı zaten bağlı."}
                else:
                    # 'to_thread' GİTTİ, yerine 'await' GELDİ
                    response_json, auth_username, auth_role = await check_login(user, pwd)

                    if response_json.get("command") == "AUTH_SUCCESS":
                        current_username = auth_username;
                        current_role = auth_role
                        authenticated_clients[websocket] = {"username": current_username, "role": current_role}

                        # --- LOGIN_DATA_PACKAGE GÖNDERİMİ ---
                        # 'to_thread' GİTTİ, yerine 'await' GELDİ
                        history_payload = []
                        try:
                            async with DB_POOL.acquire() as conn:
                                # PostgreSQL'de saat dilimi (timezone) yönetimi önemlidir.
                                # 'Europe/Istanbul' (GMT+3) olarak varsayıyoruz.
                                # Sunucunuz (Radore) farklı bir saat dilimindeyse, bunu ayarlamanız gerekir.
                                history_rows = await conn.fetch(
                                    "SELECT sender_username, message_text, (timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Istanbul')::TIMESTAMP(0)::TEXT AS timestamp FROM public_messages ORDER BY timestamp DESC LIMIT 20"
                                )

                            dm_history = [f"[{row['timestamp']} - {row['sender_username']}]: {row['message_text']}" for
                                          row in history_rows]

                            history_rows.reverse()
                            # fetchrow 'Record' objesi döndürür, 'row[0]' yerine 'row['sender_username']' kullanılabilir
                            history_payload = [f"[{row['timestamp']} - {row['sender_username']}]: {row['message_text']}"
                                               for row in history_rows]
                        except Exception as e:
                            print(f"Sohbet geçmişi yüklenirken hata: {e}", file=sys.stderr)

                        user_list = list(authenticated_clients.values())

                        login_data_package = {"command": "LOGIN_DATA_PACKAGE",
                                              "payload": {"username": current_username, "role": current_role,
                                                          "history": history_payload, "user_list": user_list}}
                        await websocket.send(json.dumps(login_data_package))
                        print(f"Giriş başarılı: {current_username} (Rol: {current_role}).")
                        break

            else:
                response_json = {"command": "AUTH_FAIL", "payload": "Geçersiz komut."}
            if response_json:
                await websocket.send(json.dumps(response_json))

                # --- YENİ EKLENEN BLOK ---
                # Eğer yanıt bir 'HATA' ise, istemciyi sıfırlamaya zorla
                if "FAIL" in response_json.get("command", ""):
                    print(f"DEBUG: Kimlik doğrulama başarısız, bağlantı kapatılıyor.")
                    await websocket.close(code=1000, reason="Authentication failed")
                    break  # Kimlik doğrulama döngüsünden çık
                # --- YENİ BLOK SONU ---

            if not current_username:
                return

        # --- AŞAMA 2: SOHBET DÖNGÜSÜ ---
        join_msg = {"command": "SYS_MSG", "payload": f"[{current_username}] sohbete katıldı!"};
        await broadcast(join_msg, exclude_websocket=websocket);
        await broadcast_user_list(exclude_websocket=websocket)

        # --- Handler içinde websocket scope ---
        AUDIO_SAVE_DIR = "audio_records"
        os.makedirs(AUDIO_SAVE_DIR, exist_ok=True)
        user_audio_buffer = []  # sadece bu websocket için buffer

        # --- AŞAMA 2: SOHBET DÖNGÜSÜ (NİHAİ - DÜZELTİLMİŞ) ---
        join_msg = {"command": "SYS_MSG", "payload": f"[{current_username}] sohbete katıldı!"};
        await broadcast(join_msg, exclude_websocket=websocket);
        await broadcast_user_list(exclude_websocket=websocket)

        async for message in websocket:

            # 1. Gelen veri ikili (ses) ise (v4.0 "Telsiz" Modeli)
            if isinstance(message, bytes):
                if current_role == 'admin':
                    await websocket.send(
                        json.dumps({"command": "SYS_MSG_ERR", "payload": "Admin hesabı ile ses gönderilemez."}));
                    continue
                # await broadcast_audio(message, exclude_websocket=websocket); # Canlı yayını kapattık, v4.1'e odaklan
                continue  # Şimdilik ikili veriyi görmezden gel

            # 2. Gelen veri metin (JSON) ise
            try:
                data = json.loads(message); command = data.get("command"); payload = data.get("payload", {})
            except json.JSONDecodeError:
                continue

            # --- TÜM KOMUTLARIN LİSTESİ (DOĞRU HİZALANMIŞ) ---

            if command == "CHAT":
                message_text = payload.get("message");
                if not message_text or len(message_text) > MAX_MESSAGE_LEN: continue
                try:
                    async with DB_POOL.acquire() as conn:
                        await conn.execute(
                            "INSERT INTO public_messages (sender_username, message_text) VALUES ($1, $2)",
                            current_username, message_text)
                except Exception as e:
                    print(f"Mesaj veritabanına kaydedilemedi: {e}", file=sys.stderr)
                timestamp = date.datetime.now().strftime('%H:%M');
                formatted_message = {"command": "CHAT",
                                     "payload": f"[{timestamp} - {current_username}]: {message_text}"};
                await broadcast(formatted_message)


            elif command == "FETCH_DM_HISTORY":
                target_user = payload.get("target")
                if not target_user:
                    continue

                print(f"DEBUG: {current_username} kullanıcısı {target_user} ile olan DM geçmişini istedi.")

                try:
                    history_payload = []
                    async with DB_POOL.acquire() as conn:
                        # Hem gönderen 'ben' alıcı 'hedef' olanları,
                        # hem de gönderen 'hedef' alıcı 'ben' olanları çek
                        # ve tarihe göre eskiden yeniye sırala
                        history_rows = await conn.fetch(
                            """
                            SELECT sender_username,
                                   target_username,
                                   message_text,
                                   (timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Istanbul') ::TIMESTAMP(0)::TEXT AS timestamp
                            FROM private_messages
                            WHERE (sender_username = $1
                              AND target_username = $2)
                               OR (sender_username = $2
                              AND target_username = $1)
                            ORDER BY timestamp ASC
                                LIMIT 50
                            """,
                            current_username, target_user
                        )

                        # Mesajları istemcinin beklediği formata dönüştür [cite: 120-125]
                        for row in history_rows:
                            sender = row['sender_username']
                            target = row['target_username']
                            msg = row['message_text']
                            ts = row['timestamp']  # Sorguda formatladık [cite: 286]

                            if sender == current_username:
                                formatted_msg = f"[{ts}] [Siz -> {target}]: {msg}"
                            else:
                                formatted_msg = f"[{ts}] [{sender} -> Siz]: {msg}"

                            history_payload.append(formatted_msg)

                    # İstemcinin "DM_HISTORY" komutuna yanıt ver [cite: 117]
                    response = {
                        "command": "DM_HISTORY",
                        "payload": {
                            "target": target_user,
                            "messages": history_payload
                        }
                    }
                    await websocket.send(json.dumps(response))

                except Exception as e:
                    print(f"DM Geçmişi alınırken hata: {e}", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)
                # --- YENİ EKLENTİ SONU ---



            elif command == "DM":
                target_user = payload.get("target");
                message_text = payload.get("message")
                if not target_user or not message_text or len(message_text) > MAX_MESSAGE_LEN or len(
                    target_user) > MAX_DM_TARGET_LEN: continue
                await handle_dm(current_username, target_user, message_text)

            elif command == "TYPING_START":
                await broadcast({"command": "TYPING_START", "payload": current_username}, exclude_websocket=websocket)
            elif command == "TYPING_STOP":
                await broadcast({"command": "TYPING_STOP", "payload": current_username}, exclude_websocket=websocket)

            elif command == "KICK":
                if current_role == 'admin':
                    await handle_kick(current_username, payload.get("target"), websocket)
                else:
                    error_msg = {"command": "SYS_MSG_ERR",
                                 "payload": "Bu komutu kullanma yetkiniz yok."}; await websocket.send(
                        json.dumps(error_msg))

            # --- v4.1 (DOSYA YÜKLEME) KOMUTLARI ---

            elif command == "AUDIO_MSG":  # <-- BLOK 1
                try:
                    filedata_b64 = payload.get("filedata_b64");
                    file_format = payload.get("format", "mp3");
                    duration = payload.get("duration_seconds", 0)
                    if not filedata_b64: continue

                    # 1. Veriyi çöz ve kaydet
                    audio_bytes = base64.b64decode(filedata_b64)
                    if len(audio_bytes) > MAX_AUDIO_SIZE:
                        await websocket.send(json.dumps(
                            {"command": "SYS_MSG_ERR", "payload": f"Ses dosyası çok büyük (Maks {MAX_AUDIO_SIZE}MB)."}))
                        continue

                    # 2. Benzersiz dosya adı oluştur ve kaydet
                    file_id = f"{uuid.uuid4()}.{file_format}";
                    save_path = os.path.join("uploads", "audio", file_id)
                    with open(save_path, "wb") as f:
                        f.write(audio_bytes)

                    # 3. Herkese "CHAT" mesajı olarak yayınla
                    print(f"Sesli mesaj alındı: {current_username} -> {file_id}")
                    timestamp = date.datetime.now().strftime('%H:%M')
                    message_text = f"[▶️ Sesli Mesaj ({duration:.1f}s) - ID: {file_id}]"
                    formatted_message = {"command": "CHAT",
                                         "payload": f"[{timestamp} - {current_username}]: {message_text}"};
                    await broadcast(formatted_message)

                except Exception as e:
                    print(f"Sesli mesaj işlenirken hata: {e}", file=sys.stderr)
                    await websocket.send(
                        json.dumps({"command": "SYS_MSG_ERR", "payload": "Sesli mesajınız işlenemedi."}))

            elif command == "FETCH_AUDIO":  # <-- BLOK 2 (ARTIK AYNI HİZADA)
                try:
                    file_id = payload.get("file_id");
                    if not file_id: continue
                    base_dir = os.path.abspath("uploads/audio");
                    file_path = os.path.abspath(os.path.join(base_dir, file_id))
                    if os.path.commonprefix((file_path, base_dir)) != base_dir: raise Exception(
                        "Güvenlik ihlali: İzin verilmeyen dosya yolu.")
                    if not os.path.exists(file_path):
                        await websocket.send(
                            json.dumps({"command": "SYS_MSG_ERR", "payload": "Ses dosyası sunucuda bulunamadı."}));
                        continue

                    with open(file_path, "rb") as f:
                        audio_data_bytes = f.read()

                    audio_base64 = base64.b64encode(audio_data_bytes).decode('utf-8')
                    message_json = {"command": "AUDIO_DATA",
                                    "payload": {"file_id": file_id, "filedata_b64": audio_base64}}
                    await websocket.send(json.dumps(message_json))

                except Exception as e:
                    print(f"Ses dosyası gönderilirken hata: {e}", file=sys.stderr)
                    await websocket.send(
                        json.dumps({"command": "SYS_MSG_ERR", "payload": f"Ses dosyası alınamadı: {e}"}))
            # --- GÖRÜNTÜLÜ ARAMA / WEBRTC SİNYAL YÖNLENDİRME (TEMİZ BLOK) ---
            elif command in ("CALL_REQUEST", "CALL_ACCEPT", "CALL_REJECT",
                             "CALL_ENDED", "VIDEO_REQUEST", "VIDEO_ACCEPT",
                             "VIDEO_REJECT", "VIDEO_ENDED"):
                await relay_signal(command, payload, current_username, websocket)

            elif command in ("CALL_OFFER", "CALL_ANSWER", "CALL_CANDIDATE"):
                target_username = payload.get("target")
                if not target_username:
                    print(f"DEBUG ({current_username}): {command} atlandı (target eksik).")
                    continue
                target_socket = next((ws for ws, d in authenticated_clients.items()
                                      if d.get("username") == target_username), None)
                if target_socket:
                    relay_payload = dict(payload)
                    relay_payload["from"] = current_username
                    await target_socket.send(json.dumps({
                        "command": command,
                        "payload": relay_payload
                    }))


            elif command == "KEY_INIT":
                target = payload.get("target")
                pub = payload.get("pub");
                salt = payload.get("salt")
                target_socket = next((s for s, d in authenticated_clients.items() if d["username"] == target), None)
                if target_socket:
                        await target_socket.send(json.dumps({"command": "KEY_INIT",
                                                             "payload": {"from_user": current_username, "pub": pub,
                                                                         "salt": salt}}))

            elif command == "KEY_REPLY":
                target = payload.get("target")
                pub = payload.get("pub")
                salt = payload.get("salt")
                target_socket = next((s for s, d in authenticated_clients.items() if d["username"] == target), None)
                if target_socket:
                        await target_socket.send(json.dumps({"command": "KEY_REPLY",
                                                             "payload": {"from_user": current_username, "pub": pub,
                                                                         "salt": salt}}))

            elif command == "ENC_MSG":
                    # DM or public depending on presence of 'target'
                target = payload.get("target")
                env = {
                        "command": "ENC_MSG",
                        "payload": {
                            "from_user": current_username,
                            "nonce": payload.get("nonce"),
                            "salt": payload.get("salt"),
                            "ct": payload.get("ct"),
                            "aad": payload.get("aad"),
                        }
                }
                if target:
                    target_socket = next((s for s, d in authenticated_clients.items() if d["username"] == target),
                                             None)
                    if target_socket:
                            await target_socket.send(json.dumps(env))
                else:
                        await broadcast(env)





            # --- Diğer Komutlar ---
            else:
                print(f"Bilinmeyen komut alındı ({current_username}): {command}")

    except (websockets.exceptions.ConnectionClosedOK, websockets.exceptions.ConnectionClosedError):
        pass  # Bağlantı kapandığında (kick, quit) sessizce çık
    except Exception as e:
        print(f"handler içinde HATA ({current_username}): {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    finally:
        if websocket in authenticated_clients:
            username = authenticated_clients.pop(websocket)["username"]
            print(f"'{username}' kullanıcısının bağlantısı kesildi.")
            leave_msg = {"command": "SYS_MSG", "payload": f"[{username}] sohbetten ayrıldı."};
            await broadcast(leave_msg);
            await broadcast_user_list()
            stop_msg = {"command": "TYPING_STOP", "payload": username};
            await broadcast(stop_msg)


# 'main' fonksiyonunun TAMAMINI bununla değiştir:

async def main():
    global DB_POOL

    stop_event = asyncio.Event()

    # --- 1. Windows dışındaki sistemlerde sinyal işleyicisi
    loop = asyncio.get_running_loop()

    if platform.system() != "Windows":
        def _on_signal():
            print("\nSinyal alındı: kapanma başlatılıyor...")
            stop_event.set()

        for s in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(s, _on_signal)
            except NotImplementedError:
                pass

    # --- 2. Veritabanı bağlantısı
    try:
        DB_POOL = await asyncpg.create_pool(
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            host=DB_HOST
        )
        print("✅ Veritabanı bağlantısı kuruldu.")
    except Exception as e:
        print(f"❌ Veritabanına bağlanılamadı: {e}", file=sys.stderr)
        traceback.print_exc()
        return

    # --- 3. SSL ayarları
    ssl_context = None
    try:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain("certs/cert.pem", "certs/key.pem")
        print("🔒 SSL sertifikası yüklendi.")
    except FileNotFoundError:
        print("⚠️ SSL devre dışı: 'certs/' klasöründe sertifika bulunamadı.")
        ssl_context = None

    # --- 4. WebSocket sunucusu başlat
    try:
        server = await websockets.serve(handler, HOST, PORT, ssl=ssl_context)
    except Exception as e:
        print(f"❌ Websocket başlatma hatası: {e}", file=sys.stderr)
        traceback.print_exc()
        await DB_POOL.close()
        return

    print(f"✅ Sunucu {HOST}:{PORT} adresinde çalışıyor. (Ctrl+C ile durdur)")

    # --- 5. Ana döngü: Ctrl+C bekle
    try:
        # Windows'ta KeyboardInterrupt ile manuel yakalama
        while not stop_event.is_set():
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        print("\n🛑 Ctrl+C alındı, kapatma işlemi başlatılıyor...")
        stop_event.set()
    finally:
        print("🔻 Sunucu kapatılıyor...")

        try:
            server.close()
            await server.wait_closed()
            print("✅ WebSocket sunucusu kapatıldı.")
        except Exception as e:
            print(f"Sunucu kapatılırken hata: {e}", file=sys.stderr)

        try:
            if DB_POOL:
                await DB_POOL.close()
                print("✅ Veritabanı bağlantı havuzu kapatıldı.")
        except Exception as e:
            print(f"DB kapatılırken hata: {e}", file=sys.stderr)

        print("🧹 Temizlik tamamlandı. Program güvenli şekilde sonlandı.")

if __name__ == "__main__":
    # Windows için loop politikası
    if platform.system() == "Windows":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass

    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Program başlatılırken hata: {e}", file=sys.stderr)
        traceback.print_exc()
    finally:
        print("💤 Programdan çıkılıyor...")
