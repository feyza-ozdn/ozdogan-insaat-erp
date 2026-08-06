import psycopg2
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from flask import Flask, jsonify, render_template, request, redirect, session, url_for

app = Flask(__name__)
app.secret_key = 'ozdogan_erp_gizli_anahtar' # Oturum yönetimi için gerekli gizli anahtar

# 1. Veritabanı Bağlantısı
def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        # Render bulut veritabanı bağlantısı
        conn = psycopg2.connect(database_url, sslmode='prefer')
    else:
        # Lokal (senin bilgisayarındaki) veritabanı bağlantısı
        conn = psycopg2.connect(
          host="localhost",
          database="insaat_erp_db",
          user="postgres",
          password="Feyzanur1414", # Şifreni girmeyi unutma!
          port="5434"
    )
    return conn

# Güvenlik Kontrolü: Giriş yapmayan hiç kimse sayfaları göremez!
@app.before_request
def require_login():
    # Giriş sayfası ve statik dosyalar hariç oturum kontrolü yap
    allowed_routes = ['login', 'static']
    if request.endpoint not in allowed_routes and 'user' not in session:
        return redirect(url_for('login'))

# Giriş Rotası
@app.route('/login', methods=['GET', 'POST'])
def login():
    hata = None
    if request.method == 'POST':
        kullanici_adi = request.form['username']
        sifre = request.form['password']
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM users WHERE username = %s AND password = %s', (kullanici_adi, sifre))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user:
            session['user'] = kullanici_adi # Oturum açıldı
            return redirect(url_for('index'))
        else:
            hata = "Kullanıcı adı veya şifre hatalı!"
            
    return render_template('login.html', hata=hata)

# Çıkış Rotası
@app.route('/logout')
def logout():
    session.pop('user', None) # Oturumu kapat
    return redirect(url_for('login'))

# 2. Merkez Bankası (TCMB) Kur Çekme Fonksiyonu
def get_tcmb_rates(date_str):
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        year_month = date_obj.strftime("%Y%m")
        day_month_year = date_obj.strftime("%d%m%Y")
        
        url = f"https://www.tcmb.gov.tr/kurlar/{year_month}/{day_month_year}.xml"
        response = requests.get(url)
        
        if response.status_code != 200:
            return {"USD": 1.0, "EUR": 1.0, "hata": "Kur verisi bulunamadı."}
            
        tree = ET.fromstring(response.content)
        usd_rate = 1.0
        eur_rate = 1.0
        
        for currency in tree.findall('Currency'):
            if currency.get('CurrencyCode') == 'USD':
                usd_rate = float(currency.find('ForexSelling').text)
            elif currency.get('CurrencyCode') == 'EUR':
                eur_rate = float(currency.find('ForexSelling').text)
                
        return {"USD": usd_rate, "EUR": eur_rate}
        
    except Exception as e:
        return {"USD": 1.0, "EUR": 1.0, "hata": str(e)}

# 3. Rotalar (Web Sayfaları ve İşlemler)

# Ana sayfa: HTML formumuzu ekrana yansıtır ve menüler için verileri çeker
@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Menüde göstermek için projeleri çekiyoruz
    cur.execute("SELECT id, name FROM projects ORDER BY id DESC")
    projeler = cur.fetchall()
    
    # Menüde göstermek için müşterileri çekiyoruz
    cur.execute("SELECT id, first_name, last_name FROM customers ORDER BY id DESC")
    musteriler = cur.fetchall()
    
    cur.close()
    conn.close()
    
    # Çekilen bu verileri index.html sayfasına gönderiyoruz
    return render_template('index.html', projeler=projeler, musteriler=musteriler)

# Formdan gelen verileri yakalayıp veritabanına kaydeder
@app.route('/ekle', methods=['POST'])
def ekle():
    tarih = request.form['tarih']
    islem_tipi = request.form['islem_tipi']
    kategori = request.form['kategori']
    tutar = float(request.form['tutar'])
    para_birimi = request.form['para_birimi']
    
    # Yeni eklenen ID verilerini formdan çekiyoruz (Boş bırakıldıysa None yapıyoruz)
    project_id = request.form['project_id'] if request.form['project_id'] else None
    customer_id = request.form['customer_id'] if request.form['customer_id'] else None
    
    kurlar = get_tcmb_rates(tarih)
    usd_rate = kurlar.get('USD', 1.0)
    eur_rate = kurlar.get('EUR', 1.0)
    
    if para_birimi == 'USD':
        amount_try = tutar * usd_rate
    elif para_birimi == 'EUR':
        amount_try = tutar * eur_rate
    else:
        amount_try = tutar
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    # INSERT sorgumuza project_id ve customer_id alanlarını da ekledik
    cur.execute('''
        INSERT INTO transactions 
        (transaction_date, transaction_type, category, amount, currency, usd_rate, eur_rate, amount_try, project_id, customer_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (tarih, islem_tipi, kategori, tutar, para_birimi, usd_rate, eur_rate, amount_try, project_id, customer_id))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return "<h1>İşlem Başarıyla Kaydedildi!</h1><br><a href='/islemler'>Finansal Raporlara Git</a> <br><br> <a href='/'>Yeni İşlem Ekle</a>"

@app.route('/islemler')
def islemler():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # En son eklenen işlem en üstte görünsün diye ORDER BY ... DESC kullanıyoruz
    cur.execute('''
        SELECT transaction_date, transaction_type, category, amount, currency, usd_rate, eur_rate, amount_try, id
        FROM transactions 
        ORDER BY transaction_date DESC
    ''')
    veriler = cur.fetchall()
    
    cur.close()
    conn.close()
    
    # Verileri 'islemler.html' sayfasına gönderiyoruz
    return render_template('islemler.html', islemler=veriler)

    # YENİ: Seçilen işlemi veritabanından silen fonksiyon
@app.route('/islem_sil/<int:id>', methods=['POST'])
def islem_sil(id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Gelen ID'ye ait işlemi kalıcı olarak sil
    cur.execute('DELETE FROM transactions WHERE id = %s', (id,))
    conn.commit()
    
    cur.close()
    conn.close()
    
    # Silme işleminden sonra listeyi tekrar yükle
    return redirect('/islemler')


    # Şantiye (Proje) Yönetim Ekranı
@app.route('/projeler', methods=['GET', 'POST'])
def projeler():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Formdan yeni şantiye ekleme talebi geldiyse (POST işlemi)
    if request.method == 'POST':
        name = request.form['name']
        location = request.form['location']
        start_date = request.form['start_date']
        status = request.form['status']
        
        cur.execute('''
            INSERT INTO projects (name, location, start_date, status)
            VALUES (%s, %s, %s, %s)
        ''', (name, location, start_date, status))
        conn.commit()
        
    # Mevcut şantiyeleri veritabanından çekip listeleme (GET işlemi)
    cur.execute('SELECT id, name, location, start_date, status FROM projects ORDER BY id DESC')
    projeler_listesi = cur.fetchall()
    
    cur.close()
    conn.close()
    
    # Verileri 'projeler.html' sayfasına gönder
    return render_template('projeler.html', projeler=projeler_listesi)

    # Müşteri (CRM) Yönetim Ekranı
@app.route('/musteriler', methods=['GET', 'POST'])
def musteriler():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Formdan yeni müşteri ekleme talebi geldiyse
    if request.method == 'POST':
        first_name = request.form['first_name']
        last_name = request.form['last_name']
        email = request.form['email']
        phone = request.form['phone']
        
        cur.execute('''
            INSERT INTO customers (first_name, last_name, email, phone)
            VALUES (%s, %s, %s, %s)
        ''', (first_name, last_name, email, phone))
        conn.commit()
        
    # Mevcut müşterileri veritabanından çekip listeleme
    cur.execute('SELECT id, first_name, last_name, email, phone FROM customers ORDER BY id DESC')
    musteri_listesi = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template('musteriler.html', musteriler=musteri_listesi)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)