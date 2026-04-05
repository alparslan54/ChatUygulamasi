UVP2 - Custom UDP VPN Protocol Implementation
Bu proje, Python ve Wintun sürücüsü kullanılarak geliştirilmiş, düşük seviyeli (low-level) bir VPN tünelleme protokolüdür. UVP2 adını verdiğim bu özel protokol; güvenli anahtar değişimi, dinamik yönlendirme ve yüksek performanslı veri iletimi odaklı tasarlanmıştır.

🚀 Öne Çıkan Özellikler
Özel Protokol Tasarımı: UDP tabanlı, düşük gecikmeli ve "Stateful" oturum yönetimi içeren özgün paket yapısı.

Wintun Entegrasyonu: Windows üzerinde en yüksek performansı sunan Wintun sürücüsü ile Layer 3 (IP) tünelleme.

Gelişmiş Kriptografi:

AES-256-GCM: Veri gizliliği ve bütünlüğü için kimlik doğrulamalı şifreleme.

HKDF (RFC 5869): HMAC tabanlı anahtar türetme fonksiyonu ile her oturum için benzersiz anahtar üretimi.

Key Rotation: Belirli aralıklarla (60sn) otomatik anahtar değişimi (Forward Secrecy benzeri koruma).

Otomatik Ağ Yönetimi: PowerShell üzerinden dinamik IP ataması, DNS yapılandırması ve varsayılan ağ geçidi (Default Gateway) yönetimi.

Sunucu Kapasitesi: Multi-client desteği, IP maskeleme (NAT) ve temel PPS (Packet Per Second) tabanlı flood koruması.

🛠️ Teknik Mimari
Proje, birbirini tamamlayan 5 temel modülden oluşmaktadır:

main.py: İstemci tarafındaki ana kontrolcü. Adaptör kurulumu ve Windows ağ ayarlarını yönetir.

vpn_server2.py: Linux tabanlı, multi-client destekli VPN sunucusu. NAT ve oturum izolasyonu sağlar.

transport.py: UDP üzerinden güvenli veri taşıma katmanı. Sıralama (Sequence) kontrolü ile "Replay Attack" koruması sağlar.

crypto.py: Tüm şifreleme ve anahtar yönetim süreçlerinin merkezidir.

protocol.py: UVP2 paket formatını (Header, MAC, Type) tanımlayan protokol arayüzü.

⚙️ Kurulum ve Kullanım
Gereksinimler
Python 3.10+

Windows tarafında wintun.dll dosyası (Proje dizininde olmalıdır).

Yönetici izinleri (Administrator / Root).

Kurulum
Bağımlılıkları yükleyin:
#########################################################3
Bash
pip install -r requirements.txt
.env dosyasını oluşturun ve yapılandırın:
###############################################3
Kod snippet'i
VPN_PSK=gizli_anahtariniz
VPN_SERVER_IP=sunucu_ip_adresiniz
################################
Çalıştırma
Sunucu (Linux):
Bash
sudo python3 vpn_server2.py
İstemci (Windows):
Bash
python main.py
#################################################3
🛡️ Güvenlik Notları
Bu proje eğitim ve konsept kanıtlama (PoC) amacıyla geliştirilmiştir.

PSK (Pre-Shared Key) bilgisi .env dosyası üzerinden yönetilir ve asla kaynak koda gömülmez.

Üretim ortamında kullanılmadan önce profesyonel bir sızma testi ve kod denetiminden geçmesi önerilir.

Not: Proje içerisinde kullanılan Wintun sürücüsü, WireGuard ekibi tarafından geliştirilen açık kaynaklı ve yüksek performanslı bir sürücüdür.
