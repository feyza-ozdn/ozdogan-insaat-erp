import psycopg2
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, request, redirect, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get(
    'SECRET_KEY',
    'ozdogan_erp_gizli_anahtar'
)


# =========================================================
# 1. VERİTABANI BAĞLANTISI
# =========================================================

def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')

    if database_url:
        # Render / bulut veritabanı
        conn = psycopg2.connect(
            database_url,
            sslmode='prefer'
        )
    else:
        # Lokal veritabanı
        conn = psycopg2.connect(
            host="localhost",
            database="insaat_erp_db",
            user="postgres",
            password="Feyzanur1414",
            port="5434"
        )

    return conn


# =========================================================
# 2. VERİTABANI HAZIRLAMA
# =========================================================

def init_db():

    conn = get_db_connection()
    cur = conn.cursor()

    # USERS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password VARCHAR(100) NOT NULL
        );
    """)

    cur.execute("""
        INSERT INTO users (username, password)
        VALUES ('feyza', '1234')
        ON CONFLICT (username) DO NOTHING;
    """)

    # Eski tablolar uygulamanın başka yerlerinde kullanılıyorsa
    # mevcut yapıyı bozmamak için IF NOT EXISTS bırakıyoruz.

    cur.execute("""
        CREATE TABLE IF NOT EXISTS musteriler (
            id SERIAL PRIMARY KEY,
            ad_soyad VARCHAR(100),
            telefon VARCHAR(50)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS islemler (
            id SERIAL PRIMARY KEY,
            baslik VARCHAR(100),
            tutar NUMERIC
        );
    """)

    # -----------------------------------------------------
    # TRANSACTIONS TABLOSUNDA GEREKLİ ALANLAR
    # -----------------------------------------------------

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS usd_rate NUMERIC;
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS eur_rate NUMERIC;
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS gold_rate NUMERIC;
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS amount_try NUMERIC;
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS amount_usd NUMERIC;
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS amount_eur NUMERIC;
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS amount_gold NUMERIC;
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS project_id INTEGER;
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS customer_id INTEGER;
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS description TEXT;
    """)

    conn.commit()

    cur.close()
    conn.close()


init_db()


# =========================================================
# 3. GİRİŞ KONTROLÜ
# =========================================================

@app.before_request
def require_login():

    allowed_routes = ['login', 'static']

    if (
        request.endpoint not in allowed_routes
        and 'user' not in session
    ):
        return redirect(url_for('login'))


# =========================================================
# 4. LOGIN
# =========================================================

@app.route('/login', methods=['GET', 'POST'])
def login():

    hata = None

    if request.method == 'POST':

        kullanici_adi = request.form['username']
        sifre = request.form['password']

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            '''
            SELECT *
            FROM users
            WHERE username = %s
            AND password = %s
            ''',
            (kullanici_adi, sifre)
        )

        user = cur.fetchone()

        cur.close()
        conn.close()

        if user:

            session['user'] = kullanici_adi

            return redirect(url_for('index'))

        else:

            hata = "Kullanıcı adı veya şifre hatalı!"

    return render_template(
        'login.html',
        hata=hata
    )


# =========================================================
# 5. LOGOUT
# =========================================================

@app.route('/logout')
def logout():

    session.pop('user', None)

    return redirect(url_for('login'))


# =========================================================
# 6. TCMB DÖVİZ KURLARI
# =========================================================

def get_tcmb_rates(date_str):

    """
    Verilen tarihin TCMB USD/EUR satış kurlarını getirir.

    Hafta sonu veya resmi tatil gibi durumlarda
    önceki geçerli iş gününü arar.
    """

    try:

        date_obj = datetime.strptime(
            date_str,
            '%Y-%m-%d'
        )

    except Exception:

        date_obj = datetime.now()

    # En fazla 7 gün geriye bak
    for i in range(8):

        current_date = date_obj - timedelta(days=i)

        year_month = current_date.strftime("%Y%m")
        day_month_year = current_date.strftime("%d%m%Y")

        url = (
            f"https://www.tcmb.gov.tr/kurlar/"
            f"{year_month}/{day_month_year}.xml"
        )

        try:

            response = requests.get(
                url,
                timeout=10
            )

            if response.status_code != 200:
                continue

            tree = ET.fromstring(
                response.content
            )

            usd_rate = None
            eur_rate = None

            for currency in tree.findall('Currency'):

                code = currency.get(
                    'CurrencyCode'
                )

                if code == 'USD':

                    forex_selling = currency.find(
                        'ForexSelling'
                    )

                    if forex_selling is not None:
                        usd_rate = float(
                            forex_selling.text
                        )

                elif code == 'EUR':

                    forex_selling = currency.find(
                        'ForexSelling'
                    )

                    if forex_selling is not None:
                        eur_rate = float(
                            forex_selling.text
                        )

            if usd_rate and eur_rate:

                return {
                    "USD": usd_rate,
                    "EUR": eur_rate,
                    "date": current_date.strftime(
                        "%Y-%m-%d"
                    )
                }

        except Exception:
            continue

    # Hiçbir veri bulunamazsa hata
    raise Exception(
        "TCMB döviz kuru bulunamadı."
    )


# =========================================================
# 7. ALTIN KURU
# =========================================================

def get_gold_rate(date_str):

    """
    Gram altın için XAU spot fiyatını TL olarak getirir.

    Öncelikle işlem tarihine ait günlük XAU/USD
    kapanış fiyatını kullanır.

    Daha sonra TCMB USD/TL kuru ile TL/gram
    karşılığını hesaplar.
    """

    try:

        target_date = datetime.strptime(
            date_str,
            '%Y-%m-%d'
        ).date()

    except Exception:

        target_date = datetime.now().date()

    # -----------------------------------------------------
    # XAUS günlük altın geçmişi
    # -----------------------------------------------------

    url = "https://xaus.com/api/v1/history"

    response = requests.get(
        url,
        timeout=10
    )

    response.raise_for_status()

    data = response.json()

    points = data.get(
        "points",
        []
    )

    gold_usd_oz = None

    # Önce tam tarihi bul
    for point in points:

        point_date = point.get("d")

        if point_date == target_date.strftime(
            "%Y-%m-%d"
        ):

            gold_usd_oz = point.get("c")

            break

    # -----------------------------------------------------
    # Hafta sonu / tatil ise önceki geçerli günü bul
    # -----------------------------------------------------

    if gold_usd_oz is None:

        valid_points = []

        for point in points:

            try:

                point_date = datetime.strptime(
                    point["d"],
                    "%Y-%m-%d"
                ).date()

                if point_date <= target_date:

                    valid_points.append(
                        (point_date, point.get("c"))
                    )

            except Exception:
                continue

        if valid_points:

            valid_points.sort(
                key=lambda x: x[0],
                reverse=True
            )

            gold_usd_oz = valid_points[0][1]

    if gold_usd_oz is None:

        raise Exception(
            "Altın kuru bulunamadı."
        )

    gold_usd_oz = float(
        gold_usd_oz
    )

    # 1 troy ounce = 31.1034768 gram
    gold_usd_gram = (
        gold_usd_oz / 31.1034768
    )

    # Aynı tarih için USD/TL
    tcmb = get_tcmb_rates(
        date_str
    )

    usd_try = tcmb["USD"]

    # Gram altının TL değeri
    gold_try_gram = (
        gold_usd_gram * usd_try
    )

    return gold_try_gram


# =========================================================
# 8. ANA SAYFA
# =========================================================

@app.route('/')
def index():

    conn = get_db_connection()
    cur = conn.cursor()

    # Projeler
    cur.execute(
        """
        SELECT id, name
        FROM projects
        ORDER BY id DESC
        """
    )

    projeler = cur.fetchall()

    # Müşteriler
    cur.execute(
        """
        SELECT id, first_name, last_name
        FROM customers
        ORDER BY id DESC
        """
    )

    musteriler = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        'index.html',
        projeler=projeler,
        musteriler=musteriler
    )


# =========================================================
# 9. YENİ FİNANSAL İŞLEM EKLE
# =========================================================

@app.route('/ekle', methods=['POST'])
def ekle():

    tarih = request.form['tarih']

    islem_tipi = request.form[
        'islem_tipi'
    ]

    kategori = request.form[
        'kategori'
    ]

    tutar = float(
        request.form['tutar']
    )

    para_birimi = request.form[
        'para_birimi'
    ]

    project_id = (
        request.form.get('project_id')
        or None
    )

    customer_id = (
        request.form.get('customer_id')
        or None
    )

    description = (
        request.form.get('description')
        or None
    )

    # -----------------------------------------------------
    # KURLARI AL
    # -----------------------------------------------------

    kurlar = get_tcmb_rates(
        tarih
    )

    usd_rate = kurlar["USD"]
    eur_rate = kurlar["EUR"]

    # Gram altın TL fiyatı
    gold_rate = get_gold_rate(
        tarih
    )

    # -----------------------------------------------------
    # TÜM PARA BİRİMLERİNE DÖNÜŞTÜR
    # -----------------------------------------------------

    amount_try = None
    amount_usd = None
    amount_eur = None
    amount_gold = None

    # -----------------------------------------------------
    # TL
    # -----------------------------------------------------

    if para_birimi == 'TRY':

        amount_try = tutar

        amount_usd = (
            tutar / usd_rate
        )

        amount_eur = (
            tutar / eur_rate
        )

        amount_gold = (
            tutar / gold_rate
        )

    # -----------------------------------------------------
    # USD
    # -----------------------------------------------------

    elif para_birimi == 'USD':

        amount_usd = tutar

        amount_try = (
            tutar * usd_rate
        )

        amount_eur = (
            amount_try / eur_rate
        )

        amount_gold = (
            amount_try / gold_rate
        )

    # -----------------------------------------------------
    # EUR
    # -----------------------------------------------------

    elif para_birimi == 'EUR':

        amount_eur = tutar

        amount_try = (
            tutar * eur_rate
        )

        amount_usd = (
            amount_try / usd_rate
        )

        amount_gold = (
            amount_try / gold_rate
        )

    # -----------------------------------------------------
    # ALTIN
    # -----------------------------------------------------

    elif para_birimi == 'ALTIN':

        # ALTIN tutarı gramdır
        amount_gold = tutar

        amount_try = (
            tutar * gold_rate
        )

        amount_usd = (
            amount_try / usd_rate
        )

        amount_eur = (
            amount_try / eur_rate
        )

    else:

        return (
            "Geçersiz para birimi!",
            400
        )

    # -----------------------------------------------------
    # VERİTABANINA KAYDET
    # -----------------------------------------------------

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        '''
        INSERT INTO transactions
        (
            transaction_date,
            transaction_type,
            category,
            amount,
            currency,
            usd_rate,
            eur_rate,
            gold_rate,
            amount_try,
            amount_usd,
            amount_eur,
            amount_gold,
            project_id,
            customer_id,
            description
        )
        VALUES
        (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s
        )
        ''',
        (
            tarih,
            islem_tipi,
            kategori,
            tutar,
            para_birimi,
            usd_rate,
            eur_rate,
            gold_rate,
            amount_try,
            amount_usd,
            amount_eur,
            amount_gold,
            project_id,
            customer_id,
            description
        )
    )

    conn.commit()

    cur.close()
    conn.close()

    return redirect(url_for('index', basarili='1'))


# =========================================================
# 10. FİNANSAL İŞLEMLER
# =========================================================

@app.route('/islemler')
def islemler():

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        '''
        SELECT
            transaction_date,
            transaction_type,
            category,
            amount,
            currency,
            usd_rate,
            eur_rate,
            gold_rate,
            amount_try,
            amount_usd,
            amount_eur,
            amount_gold,
            id
        FROM transactions
        ORDER BY transaction_date DESC
        '''
    )

    veriler = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        'islemler.html',
        islemler=veriler
    )


# =========================================================
# 11. İŞLEM SİL
# =========================================================

@app.route(
    '/islem_sil/<int:id>',
    methods=['POST']
)
def islem_sil(id):

    conn = get_db_connection()
    cur = conn.cursor()

    try:

        cur.execute(
            '''
            DELETE FROM transactions
            WHERE id = %s
            ''',
            (id,)
        )

        conn.commit()

    except Exception:

        conn.rollback()
        raise

    finally:

        cur.close()
        conn.close()

    return redirect(
        url_for('islemler')
    )


# =========================================================
# 12. ŞANTİYE / PROJE YÖNETİMİ
# =========================================================

@app.route(
    '/projeler',
    methods=['GET', 'POST']
)
def projeler():

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':

        name = request.form['name']
        location = request.form['location']
        start_date = request.form['start_date']
        status = request.form['status']

        cur.execute(
            '''
            INSERT INTO projects
            (
                name,
                location,
                start_date,
                status
            )
            VALUES (%s, %s, %s, %s)
            ''',
            (
                name,
                location,
                start_date,
                status
            )
        )

        conn.commit()

    cur.execute(
        '''
        SELECT
            id,
            name,
            location,
            start_date,
            status
        FROM projects
        ORDER BY id DESC
        '''
    )

    projeler_listesi = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        'projeler.html',
        projeler=projeler_listesi
    )


# =========================================================
# 13. MÜŞTERİLER
# =========================================================

@app.route(
    '/musteriler',
    methods=['GET', 'POST']
)
def musteriler():

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':

        first_name = request.form[
            'first_name'
        ]

        last_name = request.form[
            'last_name'
        ]

        email = request.form[
            'email'
        ]

        phone = request.form[
            'phone'
        ]

        cur.execute(
            '''
            INSERT INTO customers
            (
                first_name,
                last_name,
                email,
                phone
            )
            VALUES (%s, %s, %s, %s)
            ''',
            (
                first_name,
                last_name,
                email,
                phone
            )
        )

        conn.commit()

    cur.execute(
        '''
        SELECT
            id,
            first_name,
            last_name,
            email,
            phone
        FROM customers
        ORDER BY id DESC
        '''
    )

    musteri_listesi = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        'musteriler.html',
        musteriler=musteri_listesi
    )


# =========================================================
# 14. MÜŞTERİ SİL
# =========================================================

@app.route(
    '/musteri_sil/<int:id>',
    methods=['POST']
)
def musteri_sil(id):

    conn = get_db_connection()
    cur = conn.cursor()

    try:

        # Müşteriye bağlı işlemleri silme.
        # Sadece müşteri bağlantısını kaldır.

        cur.execute(
            '''
            UPDATE transactions
            SET customer_id = NULL
            WHERE customer_id = %s
            ''',
            (id,)
        )

        cur.execute(
            '''
            DELETE FROM customers
            WHERE id = %s
            ''',
            (id,)
        )

        conn.commit()

    except Exception:

        conn.rollback()
        raise

    finally:

        cur.close()
        conn.close()

    return redirect(
        url_for('musteriler')
    )


# =========================================================
# 15. UYGULAMAYI ÇALIŞTIR
# =========================================================

if __name__ == '__main__':

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host='0.0.0.0',
        port=port,
        debug=True
    )
