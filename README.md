Tabii ki, kopyalaması en kolay olacak şekilde, tüm README.md içeriğini aşağıda düz metin olarak hazırladım. Bu içeriği direkt seçip README.md dosyanın içine yapıştırabilirsin.

UVP2 - Custom UDP VPN Protocol Implementation
Bu proje, Python ve Wintun sürücüsü kullanılarak geliştirilmiş, düşük seviyeli (low-level) bir VPN tünelleme çözümüdür. UVP2 protokolü; eğitim ve ağ güvenliği araştırmaları kapsamında, güvenli veri iletimi ve dinamik ağ yönetimi prensiplerini uygulamak amacıyla tasarlanmıştır.

🚀 Öne Çıkan Özellikler
Özel Protokol Tasarımı (UVP2): UDP tabanlı, düşük gecikmeli ve oturum (session) yönetimli özgün paket yapısı.

Wintun Sürücü Entegrasyonu: Windows sistemlerde yüksek performanslı Layer 3 (IP) tünelleme ve veri yakalama.

Gelişmiş Kriptografik Katman:

AES-256-GCM: Veri gizliliği ve bütünlüğü için kimlik doğrulamalı şifreleme (Authenticated Encryption).

HKDF (RFC 5869): HMAC tabanlı anahtar türetme fonksiyonu ile her oturum için benzersiz anahtar üretimi.

Dinamik Anahtar Rotasyonu: 60 saniyelik aralıklarla otomatik anahtar yenileme (Perfect Forward Secrecy prensibi).

Otomatik Ağ Konfigürasyonu: PowerShell üzerinden dinamik IP atama, DNS yapılandırma ve varsayılan ağ geçidi yönetimi.

Sunucu Altyapısı: Multi-client desteği, IP maskeleme (NAT) ve PPS tabanlı temel flood koruması.

🛠️ Teknik Mimari
Proje beş ana bileşenden oluşmaktadır:

main.py: İstemci tarafı kontrolcü; adaptör kurulumu ve Windows ağ ayarlarını yönetir.

vpn_server2.py: Linux tabanlı sunucu; istemci oturumlarını yönetir ve trafiği NAT üzerinden internete aktarır.

transport.py: UDP üzerinden güvenli taşıma katmanı; Replay Attack koruması ve oturum takibi sağlar.

crypto.py: Şifreleme, anahtar türetme ve rotasyon işlemlerini gerçekleştiren güvenlik modülü.

protocol.py: UVP2 paket formatını tanımlayan düşük seviyeli protokol arayüzü.

⚙️ Kurulum ve Kullanım
Gereksinimler
Python 3.10+

Windows işletim sistemi (İstemci için) ve wintun.dll dosyası.

Yönetici (Administrator/Root) izinleri.

Kurulum Adımları
Bağımlılıkları yükleyin:
pip install -r requirements.txt

.env dosyasını yapılandırın (Güvenlik nedeniyle asla paylaşılmamalıdır):
VPN_PSK=gizli_anahtariniz
VPN_SERVER_IP=sunucu_ip_adresiniz

Çalıştırma
Sunucu: sudo python3 vpn_server2.py

İstemci: python main.py

🛡️ Güvenlik ve Uyarılar
Bu proje konsept kanıtlama (PoC) amacıyla geliştirilmiştir.

Gizli anahtar (PSK) yönetimi çevre değişkenleri (.env) üzerinden yapılır ve kaynak koda dahil edilmez.
