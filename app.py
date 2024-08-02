# app.py

import os
import tweepy
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from werkzeug.utils import secure_filename
import logging
from logging.handlers import RotatingFileHandler
import sqlite3
import random
import time
from cryptography.fernet import Fernet
import threading
from datetime import datetime, timedelta
import asyncio

app = Flask(__name__)
app.secret_key = os.urandom(24)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

# ロギングの設定
logging.basicConfig(level=logging.DEBUG)
file_handler = RotatingFileHandler('app.log', maxBytes=10000, backupCount=3)
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
app.logger.addHandler(file_handler)

# ストリームハンドラーの追加（コンソール出力用）
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)
stream_handler.setFormatter(formatter)
app.logger.addHandler(stream_handler)

app.logger.info("アプリケーションの初期化を開始")

# アップロードされたファイルの保存先
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# アップロードフォルダが存在しない場合は作成
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
    app.logger.info(f"アップロードフォルダを作成しました: {UPLOAD_FOLDER}")

# 暗号化キーの生成（実際の運用では安全に管理する必要があります）
ENCRYPTION_KEY = Fernet.generate_key()
cipher_suite = Fernet(ENCRYPTION_KEY)

def encrypt_data(data):
    return cipher_suite.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data):
    return cipher_suite.decrypt(encrypted_data.encode()).decode()

def init_db():
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS videos
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  filename TEXT NOT NULL,
                  caption TEXT NOT NULL,
                  reply_content TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS twitter_accounts
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT NOT NULL,
                  consumer_key TEXT NOT NULL,
                  consumer_secret TEXT NOT NULL,
                  access_token TEXT NOT NULL,
                  access_token_secret TEXT NOT NULL,
                  post_flag INTEGER NOT NULL)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS activity_log
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp DATETIME NOT NULL,
                  account TEXT NOT NULL,
                  action TEXT NOT NULL,
                  result TEXT NOT NULL)''')
    
    conn.commit()
    conn.close()
    app.logger.info("データベースの初期化が完了しました")

# アプリケーション起動時にデータベースを初期化
init_db()

def insert_video_data(filename, caption, reply_content):
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO videos (filename, caption, reply_content) VALUES (?, ?, ?)", 
                  (filename, caption, reply_content))
        conn.commit()
        app.logger.info(f"データを挿入しました: {filename}")
    except sqlite3.Error as e:
        app.logger.error(f"データ挿入中にエラーが発生しました: {str(e)}")
    finally:
        conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        app.logger.info("POSTリクエストを受信しました")
        if 'file' not in request.files:
            app.logger.warning("ファイルがリクエストに含まれていません")
            flash('ファイルがありません')
            return redirect(request.url)
        file = request.files['file']
        caption = request.form.get('caption', '')
        reply_content = request.form.get('reply_content', '')
        if file.filename == '':
            app.logger.warning("ファイル名が空です")
            flash('ファイルが選択されていません')
            return redirect(request.url)
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            app.logger.info(f"ファイルをアップロードしました: {filepath}")
            
            # データベースに保存
            insert_video_data(filename, caption, reply_content)
            
            flash('動画、キャプション、リプライ内容が保存されました')
            return redirect(url_for('index'))
    return render_template('index.html')

@app.route('/videos', methods=['GET'])
def get_videos():
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    c.execute("SELECT * FROM videos")
    videos = c.fetchall()
    conn.close()
    app.logger.info(f"動画リストを取得しました: {len(videos)}件")
    return jsonify([{'id': v[0], 'filename': v[1], 'caption': v[2], 'reply_content': v[3]} for v in videos])

@socketio.on('connect')
def handle_connect():
    app.logger.info(f"クライアントが接続しました: {request.sid}")
    emit('status', {'message': 'サーバーに接続しました'})

@socketio.on('disconnect')
def handle_disconnect():
    app.logger.info(f"クライアントが切断しました: {request.sid}")

# グローバル変数で自動投稿の状態を管理
auto_posting_thread = None
auto_posting_interval = 0
next_post_time = None
current_status = "待機中"

def update_status(new_status):
    global current_status
    current_status = new_status
    socketio.emit('status_update', {'status': current_status})
    app.logger.info(f"ステータスを更新しました: {new_status}")

def log_activity(account, action, result):
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO activity_log (timestamp, account, action, result) VALUES (?, ?, ?, ?)",
                  (timestamp, account, action, result))
        conn.commit()
        app.logger.info(f"アクティビティログを追加しました: {account} - {action} - {result}")
    except sqlite3.Error as e:
        app.logger.error(f"アクティビティログの追加中にエラーが発生しました: {str(e)}")
    finally:
        conn.close()

def get_recent_activities(limit=10):
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    c.execute("SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?", (limit,))
    activities = c.fetchall()
    conn.close()
    app.logger.info(f"最近のアクティビティを取得しました: {len(activities)}件")
    return [{'timestamp': a[1], 'account': a[2], 'action': a[3], 'result': a[4]} for a in activities]

# 修正: アクティブなアカウントを取得する関数
def get_active_accounts():
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    c.execute("SELECT * FROM twitter_accounts WHERE post_flag = 1")
    active_accounts = c.fetchall()
    conn.close()
    app.logger.info(f"アクティブなアカウントを取得しました: {len(active_accounts)}件")
    return active_accounts

# 修正: ランダムに動画、キャプション、リプライ内容を選択する関数
def get_random_post_content():
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    c.execute("SELECT filename, caption, reply_content FROM videos ORDER BY RANDOM() LIMIT 1")
    result = c.fetchone()
    conn.close()
    if result is None:
        app.logger.error("保存された動画がありません")
        raise Exception("保存された動画がありません")
    app.logger.info(f"ランダムに投稿内容を選択しました: {result[0]}")
    return result

# 修正: Tweepy API初期化関数
def initialize_tweepy(account):
    username, consumer_key, consumer_secret, access_token, access_token_secret = account[1:6]
    auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
    auth.set_access_token(access_token, access_token_secret)
    api = tweepy.API(auth)
    client = tweepy.Client(
        consumer_key=consumer_key, consumer_secret=consumer_secret,
        access_token=access_token, access_token_secret=access_token_secret
    )
    app.logger.info(f"{username}のTweepy APIを初期化しました")
    return api, client

# 修正: 単一アカウントの投稿処理
async def post_tweet_for_account(account):
    username = account[1]
    app.logger.info(f"{username}の投稿処理を開始します")
    try:
        filename, caption, reply_content = get_random_post_content()
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"動画ファイルが見つかりません: {video_path}")

        api, client = initialize_tweepy(account)

        app.logger.info(f"{username}の動画アップロードを開始します")
        media = api.media_upload(filename=video_path, media_category='tweet_video')
        app.logger.info(f"{username}の動画アップロードが完了しました。メディアID: {media.media_id}")

        await asyncio.sleep(5)  # メディアのアップロードが完了するまで待機

        app.logger.info(f"{username}のメインツイートの投稿を開始します")
        tweet = client.create_tweet(text=caption, media_ids=[media.media_id])
        tweet_id = tweet.data['id']
        app.logger.info(f"{username}のメインツイートの投稿が完了しました。ツイートID: {tweet_id}")

        await asyncio.sleep(60)  # メイン投稿とリプライの間に時間を空ける

        app.logger.info(f"{username}のリプライの投稿を開始します")
        reply = client.create_tweet(text=reply_content, in_reply_to_tweet_id=tweet_id)
        reply_id = reply.data['id']
        app.logger.info(f"{username}のリプライの投稿が完了しました。リプライID: {reply_id}")

        log_activity(username, "ツイート投稿", "成功")
        return True
    except Exception as e:
        app.logger.error(f"{username}の投稿処理中にエラーが発生しました: {str(e)}", exc_info=True)
        log_activity(username, "ツイート投稿", f"失敗: {str(e)}")
        return False

# 修正: メイン投稿処理
async def post_tweet():
    global current_status, next_post_time
    app.logger.info("一括ツイート投稿処理を開始します")
    update_status("一括ツイート処理中")
    
    active_accounts = get_active_accounts()
    if not active_accounts:
        app.logger.error("アクティブなTwitterアカウントがありません")
        update_status("エラー: アクティブなアカウントがありません")
        return False

    tasks = [post_tweet_for_account(account) for account in active_accounts]
    results = await asyncio.gather(*tasks)

    success_count = sum(results)
    total_count = len(results)
    app.logger.info(f"一括ツイート投稿処理が完了しました。成功: {success_count}/{total_count}")
    
    update_status("待機中")
    if auto_posting_thread:
        next_post_time = datetime.now() + timedelta(seconds=auto_posting_interval)
    
    return True

def auto_post_tweet():
    global auto_posting_thread, next_post_time
    while auto_posting_thread:
        asyncio.run(post_tweet())
        socketio.emit('status', {'message': '一括ツイートが完了しました'})
        time.sleep(auto_posting_interval)

@socketio.on('post_tweet')
def handle_post_tweet():
    success = asyncio.run(post_tweet())
    if success:
        emit('status', {'message': '一括ツイートが完了しました'})
    else:
        emit('status', {'message': 'ツイートの投稿に失敗しました'})

@socketio.on('start_auto_posting')
def start_auto_posting(data):
    global auto_posting_thread, auto_posting_interval, next_post_time
    interval = data.get('interval', 0)
    if interval <= 0:
        emit('status', {'message': '無効な間隔です。正の整数を指定してください。'})
        return
    
    auto_posting_interval = interval * 60  # 分を秒に変換
    
    if auto_posting_thread is None:
        auto_posting_thread = threading.Thread(target=auto_post_tweet)
        auto_posting_thread.start()
        next_post_time = datetime.now() + timedelta(seconds=auto_posting_interval)
        app.logger.info(f"自動投稿を開始しました。間隔: {interval}分")
        emit('status', {'message': f'自動投稿を開始しました。間隔: {interval}分'})
        log_activity("System", "自動投稿開始", f"間隔: {interval}分")
        update_status("自動投稿中")
    else:
        emit('status', {'message': '自動投稿は既に実行中です。'})

@socketio.on('stop_auto_posting')
def stop_auto_posting():
    global auto_posting_thread, next_post_time
    if auto_posting_thread:
        auto_posting_thread = None
        next_post_time = None
        app.logger.info("自動投稿を停止しました。")
        emit('status', {'message': '自動投稿を停止しました。'})
        log_activity("System", "自動投稿停止", "ユーザーにより停止されました")
        update_status("待機中")
    else:
        emit('status', {'message': '自動投稿は実行されていません。'})

@socketio.on('get_app_status')
def get_app_status():
    global current_status, auto_posting_interval, next_post_time
    status = {
        'current_status': current_status,
        'auto_posting_active': auto_posting_thread is not None,
        'auto_posting_interval': auto_posting_interval // 60,  # 秒を分に変換
        'next_post_time': next_post_time.isoformat() if next_post_time else None,
        'recent_activities': get_recent_activities()
    }
    emit('app_status', status)
    app.logger.info(f"アプリケーションの状態を送信しました: {status}")

@app.route('/manage_posts')
def manage_posts():
    return render_template('manage_posts.html')

@app.route('/api/posts', methods=['GET'])
def get_posts():
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    c.execute("SELECT * FROM videos")
    posts = c.fetchall()
    conn.close()
    return jsonify([{'id': p[0], 'filename': p[1], 'caption': p[2], 'reply_content': p[3]} for p in posts])

@app.route('/api/posts', methods=['POST'])
def add_post():
    if 'file' not in request.files:
        return jsonify({'error': 'ファイルがありません'}), 400
    file = request.files['file']
    caption = request.form.get('caption', '')
    reply_content = request.form.get('reply_content', '')
    if file.filename == '':
        return jsonify({'error': 'ファイルが選択されていません'}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        insert_video_data(filename, caption, reply_content)
        log_activity("System", "投稿追加", f"ファイル名: {filename}")
        return jsonify({'message': '投稿が追加されました', 'filename': filename}), 201
    return jsonify({'error': '許可されていないファイル形式です'}), 400

@app.route('/api/posts/<int:post_id>', methods=['GET'])
def get_post(post_id):
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    c.execute("SELECT * FROM videos WHERE id = ?", (post_id,))
    post = c.fetchone()
    conn.close()
    if post:
        return jsonify({'id': post[0], 'filename': post[1], 'caption': post[2], 'reply_content': post[3]})
    return jsonify({'error': '投稿が見つかりません'}), 404

@app.route('/api/posts/<int:post_id>', methods=['PUT'])
def update_post(post_id):
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    
    # 既存の投稿を取得
    c.execute("SELECT * FROM videos WHERE id = ?", (post_id,))
    existing_post = c.fetchone()
    if not existing_post:
        conn.close()
        return jsonify({'error': '投稿が見つかりません'}), 404

    caption = request.form.get('caption', existing_post[2])
    reply_content = request.form.get('reply_content', existing_post[3])
    
    if 'file' in request.files:
        file = request.files['file']
        if file.filename != '' and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            # 古いファイルを削除
            old_filepath = os.path.join(app.config['UPLOAD_FOLDER'], existing_post[1])
            if os.path.exists(old_filepath):
                os.remove(old_filepath)
        else:
            filename = existing_post[1]
    else:
        filename = existing_post[1]

    c.execute("UPDATE videos SET filename = ?, caption = ?, reply_content = ? WHERE id = ?",
              (filename, caption, reply_content, post_id))
    conn.commit()
    conn.close()
    
    log_activity("System", "投稿更新", f"投稿ID: {post_id}")
    return jsonify({'message': '投稿が更新されました', 'id': post_id})

@app.route('/api/posts/<int:post_id>', methods=['DELETE'])
def delete_post(post_id):
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    
    # 投稿を取得
    c.execute("SELECT filename FROM videos WHERE id = ?", (post_id,))
    post = c.fetchone()
    if not post:
        conn.close()
        return jsonify({'error': '投稿が見つかりません'}), 404

    # データベースから削除
    c.execute("DELETE FROM videos WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()

    # ファイルを削除
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], post[0])
    if os.path.exists(filepath):
        os.remove(filepath)

    log_activity("System", "投稿削除", f"投稿ID: {post_id}")
    return jsonify({'message': '投稿が削除されました', 'id': post_id})

@app.route('/manage_accounts')
def manage_accounts():
    return render_template('manage_accounts.html')

@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    c.execute("SELECT id, username, post_flag FROM twitter_accounts")
    accounts = c.fetchall()
    conn.close()
    return jsonify([{'id': a[0], 'username': a[1], 'post_flag': a[2]} for a in accounts])

@app.route('/api/accounts', methods=['POST'])
def add_account():
    data = request.json
    username = data.get('username')
    consumer_key = data.get('consumer_key')
    consumer_secret = data.get('consumer_secret')
    access_token = data.get('access_token')
    access_token_secret = data.get('access_token_secret')
    post_flag = data.get('post_flag', 0)

    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO twitter_accounts 
            (username, consumer_key, consumer_secret, access_token, access_token_secret, post_flag) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (username, consumer_key, consumer_secret, access_token, access_token_secret, post_flag))
        conn.commit()
        account_id = c.lastrowid
        app.logger.info(f"新しいTwitterアカウントを追加しました: {username}")
        log_activity("System", "アカウント追加", f"ユーザー名: {username}")
        return jsonify({'message': 'アカウントが追加されました', 'id': account_id}), 201
    except sqlite3.Error as e:
        app.logger.error(f"アカウント追加中にエラーが発生しました: {str(e)}")
        return jsonify({'error': 'アカウントの追加に失敗しました'}), 500
    finally:
        conn.close()

@app.route('/api/accounts/<int:account_id>', methods=['GET'])
def get_account(account_id):
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    c.execute("SELECT id, username, post_flag FROM twitter_accounts WHERE id = ?", (account_id,))
    account = c.fetchone()
    conn.close()
    if account:
        return jsonify({'id': account[0], 'username': account[1], 'post_flag': account[2]})
    return jsonify({'error': 'アカウントが見つかりません'}), 404

@app.route('/api/accounts/<int:account_id>', methods=['PUT'])
def update_account(account_id):
    data = request.json
    username = data.get('username')
    consumer_key = data.get('consumer_key')
    consumer_secret = data.get('consumer_secret')
    access_token = data.get('access_token')
    access_token_secret = data.get('access_token_secret')
    post_flag = data.get('post_flag')

    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    try:
        c.execute("""
            UPDATE twitter_accounts 
            SET username = ?, consumer_key = ?, consumer_secret = ?, 
                access_token = ?, access_token_secret = ?, post_flag = ?
            WHERE id = ?
        """, (username, consumer_key, consumer_secret, access_token, access_token_secret, post_flag, account_id))
        conn.commit()
        app.logger.info(f"Twitterアカウントを更新しました: {username}")
        log_activity("System", "アカウント更新", f"アカウントID: {account_id}")
        return jsonify({'message': 'アカウントが更新されました', 'id': account_id})
    except sqlite3.Error as e:
        app.logger.error(f"アカウント更新中にエラーが発生しました: {str(e)}")
        return jsonify({'error': 'アカウントの更新に失敗しました'}), 500
    finally:
        conn.close()

@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    try:
        c.execute("DELETE FROM twitter_accounts WHERE id = ?", (account_id,))
        conn.commit()
        if c.rowcount == 0:
            return jsonify({'error': 'アカウントが見つかりません'}), 404
        app.logger.info(f"Twitterアカウントを削除しました: ID {account_id}")
        log_activity("System", "アカウント削除", f"アカウントID: {account_id}")
        return jsonify({'message': 'アカウントが削除されました', 'id': account_id})
    except sqlite3.Error as e:
        app.logger.error(f"アカウント削除中にエラーが発生しました: {str(e)}")
        return jsonify({'error': 'アカウントの削除に失敗しました'}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    app.logger.info("アプリケーションを起動します")
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)