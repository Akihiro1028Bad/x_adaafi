import os
import sys
import time
import tweepy
import json
import requests
from requests_oauthlib import OAuth1
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from werkzeug.utils import secure_filename
import logging
from logging.handlers import RotatingFileHandler
import sqlite3
import random
from cryptography.fernet import Fernet
import threading
from datetime import datetime, timedelta
from requests.exceptions import RequestException
from moviepy.editor import VideoFileClip
import tempfile

app = Flask(__name__)
app.secret_key = os.urandom(24)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

# ロギングの設定
logging.basicConfig(level=logging.DEBUG)
file_handler = RotatingFileHandler('app.log', maxBytes=10000000000000000, backupCount=3)
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

def process_video(video_path, max_duration=120):
    """
    動画を処理し、必要に応じて切り取ります。
    :param video_path: 元の動画ファイルのパス
    :param max_duration: 最大許容時間（秒）
    :return: 処理された動画のパス
    """
    clip = VideoFileClip(video_path)
    
    if clip.duration <= max_duration:
        clip.close()
        return video_path
    
    app.logger.info(f"動画が{max_duration}秒を超えています。切り取りを行います。")
    
    # 動画を切り取る
    cut_clip = clip.subclip(0, max_duration)
    
    # 一時ファイルを作成
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_file:
        temp_path = temp_file.name
    
    # 切り取った動画を保存
    cut_clip.write_videofile(temp_path, codec="libx264", audio_codec="aac")
    
    clip.close()
    cut_clip.close()
    
    return temp_path

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

# Twitter API関連の設定
MEDIA_ENDPOINT_URL = 'https://upload.twitter.com/1.1/media/upload.json'
POST_TWEET_URL = 'https://api.twitter.com/2/tweets'

class VideoTweet:
    def __init__(self, file_name, oauth):
        self.video_filename = file_name
        self.total_bytes = os.path.getsize(self.video_filename)
        self.media_id = None
        self.processing_info = None
        self.oauth = oauth
        self.upload_start_time = None
        app.logger.info(f"VideoTweetインスタンスを初期化: ファイル名={file_name}, サイズ={self.total_bytes}バイト")

    def upload_init(self):
        app.logger.info('INITリクエストを開始')
        request_data = {
            'command': 'INIT',
            'media_type': 'video/mp4',
            'total_bytes': self.total_bytes,
            'media_category': 'tweet_video'
        }
        req = requests.post(url=MEDIA_ENDPOINT_URL, data=request_data, auth=self.oauth)
        media_id = req.json()['media_id']
        self.media_id = media_id
        self.upload_start_time = time.time()
        app.logger.info(f'Media ID: {str(media_id)}')
        return req

    def upload_chunk(self, chunk, segment_id):
        max_retries = 5
        retry_delay = 10

        for attempt in range(max_retries):
            try:
                request_data = {
                    'command': 'APPEND',
                    'media_id': self.media_id,
                    'segment_index': segment_id
                }
                files = {
                    'media': chunk
                }
                app.logger.debug(f'APPEND: チャンク {segment_id + 1} リクエスト送信')
                req = requests.post(url=MEDIA_ENDPOINT_URL, data=request_data, files=files, auth=self.oauth, timeout=60)
                app.logger.debug(f'APPEND: チャンク {segment_id + 1} レスポンス受信: ステータスコード {req.status_code}')
                
                if req.status_code in [200, 204]:  # 200と204の両方を成功として扱う
                    app.logger.info(f'APPEND: チャンク {segment_id + 1} アップロード成功')
                    return segment_id
                else:
                    app.logger.error(f"APPENDリクエスト中にエラーが発生: ステータスコード {req.status_code}, レスポンス {req.text}")
                    if attempt < max_retries - 1:
                        app.logger.info(f"チャンク {segment_id + 1}: {retry_delay}秒後にリトライします...")
                        time.sleep(retry_delay)
                    else:
                        app.logger.error(f"チャンク {segment_id + 1}: 最大リトライ回数に達しました。")
                        return None
            except RequestException as e:
                app.logger.error(f"チャンク {segment_id + 1} リクエスト中にエラーが発生しました: {str(e)}")
                if attempt < max_retries - 1:
                    app.logger.info(f"チャンク {segment_id + 1}: {retry_delay}秒後にリトライします...")
                    time.sleep(retry_delay)
                else:
                    app.logger.error(f"チャンク {segment_id + 1}: 最大リトライ回数に達しました。")
                    return None
        return None

    def upload_append(self):
        app.logger.info('APPENDリクエストを開始')
        segment_id = 0
        bytes_sent = 0
        
        with open(self.video_filename, 'rb') as file:
            while bytes_sent < self.total_bytes:
                chunk = file.read(2*1024*1024)  # 2MBチャンク
                if not chunk:
                    break
                
                app.logger.info(f'チャンク {segment_id + 1} のアップロードを開始 (サイズ: {len(chunk)} バイト)')
                result = self.upload_chunk(chunk, segment_id)
                
                if result is not None:
                    bytes_sent += len(chunk)
                    app.logger.info(f'チャンク {segment_id + 1} のアップロードが成功 (合計: {bytes_sent} バイト)')
                    
                    if bytes_sent % (10 * 1024 * 1024) == 0 or bytes_sent == self.total_bytes:
                        app.logger.info(f'{bytes_sent} / {self.total_bytes} バイトアップロード完了 ({bytes_sent/self.total_bytes*100:.2f}%)')
                else:
                    app.logger.error(f"チャンク {segment_id + 1} のアップロードに失敗しました。アップロードを中止します。")
                    return False
                
                segment_id += 1

        app.logger.info('アップロードチャンクが完了しました')
        return True
    
    def upload_finalize(self):
        app.logger.info('FINALIZEリクエストを開始')
        request_data = {
            'command': 'FINALIZE',
            'media_id': self.media_id
        }
        req = requests.post(url=MEDIA_ENDPOINT_URL, data=request_data, auth=self.oauth)
        app.logger.debug(f"FINALIZEレスポンス: {req.json()}")
        self.processing_info = req.json().get('processing_info', None)
        self.check_status()

    def check_status(self):
        if self.processing_info is None:
            return
        state = self.processing_info['state']
        app.logger.info(f'メディア処理状態: {state}')
        if state == 'succeeded':
            return
        if state == 'failed':
            app.logger.error('メディア処理に失敗しました')
            return
        check_after_secs = self.processing_info['check_after_secs']
        app.logger.info(f'{check_after_secs} 秒後に再チェックします')
        time.sleep(check_after_secs)
        app.logger.info('ステータスチェック')
        request_params = {
            'command': 'STATUS',
            'media_id': self.media_id
        }
        req = requests.get(url=MEDIA_ENDPOINT_URL, params=request_params, auth=self.oauth)
        self.processing_info = req.json().get('processing_info', None)
        self.check_status()

    def tweet(self, client, caption):
        app.logger.info('ツイートを投稿します')
        try:
            response = client.create_tweet(text=caption, media_ids=[self.media_id])
            if response.data:
                tweet_id = response.data['id']
                app.logger.info(f"メディア付きツイートの投稿に成功しました! ツイートID: {tweet_id}")
                return tweet_id
            else:
                app.logger.error("メディア付きツイートの投稿に失敗しました")
                return None
        except Exception as e:
            app.logger.error(f"ツイート投稿中にエラーが発生しました: {str(e)}")
            return None

def post_tweet_main_riply(video_filename, caption, reply_content, account):
    app.logger.info(f"ツイート投稿プロセスを開始: ファイル名={video_filename}")
    
    oauth = OAuth1(
        account['consumer_key'],
        client_secret=account['consumer_secret'],
        resource_owner_key=account['access_token'],
        resource_owner_secret=account['access_token_secret']
    )

    video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_filename)
    processed_video_path = process_video(video_path)
    video_tweet = VideoTweet(processed_video_path, oauth)

    try:
        init_response = video_tweet.upload_init()
        if init_response.status_code != 202:
            app.logger.error(f"INITリクエストが失敗しました: {init_response.status_code}")
            return False

        if not video_tweet.upload_append():
            app.logger.error("APPENDリクエストが失敗しました")
            return False

        video_tweet.upload_finalize()

        client = tweepy.Client(
            consumer_key=account['consumer_key'],
            consumer_secret=account['consumer_secret'],
            access_token=account['access_token'],
            access_token_secret=account['access_token_secret']
        )

        tweet_id = video_tweet.tweet(client, caption)
        if tweet_id:
            app.logger.info("ツイートが正常に投稿されました")
            # リプライの投稿
            if reply_content:
                try:
                    time.sleep(10)  # 2分待機
                    reply_response = client.create_tweet(text=reply_content, in_reply_to_tweet_id=tweet_id)

                    if reply_response.data:
                        app.logger.info(f"リプライの投稿に成功しました! リプライID: {reply_response.data['id']}")
                    else:
                        app.logger.error("リプライの投稿に失敗しました")
                except Exception as e:
                    app.logger.error(f"リプライ投稿中にエラーが発生しました: {str(e)}")
            return True
        else:
            app.logger.error("ツイートの投稿に失敗しました")
            return False

    except Exception as e:
        app.logger.error(f"ツイート投稿プロセス中にエラーが発生しました: {str(e)}")
        return False
    
    finally:
        # 処理された動画が元の動画と異なる場合、一時ファイルを削除
        if processed_video_path != video_path:
            os.remove(processed_video_path)

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

def get_active_accounts():
    conn = sqlite3.connect('videos.db')
    c = conn.cursor()
    c.execute("SELECT * FROM twitter_accounts WHERE post_flag = 1")
    active_accounts = c.fetchall()
    conn.close()
    app.logger.info(f"アクティブなアカウントを取得しました: {len(active_accounts)}件")
    return active_accounts

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

def post_tweet_for_account(account):
    username = account[1]
    app.logger.info(f"{username}の投稿処理を開始します")
    try:
        filename, caption, reply_content = get_random_post_content()
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"動画ファイルが見つかりません: {video_path}")

        api, client = initialize_tweepy(account)

        account_dict = {
            'consumer_key': account[2],
            'consumer_secret': account[3],
            'access_token': account[4],
            'access_token_secret': account[5]
        }

        success = post_tweet_main_riply(filename, caption, reply_content, account_dict)
        
        if success:
            log_activity(username, "ツイート投稿", "成功")
            return True
        else:
            log_activity(username, "ツイート投稿", "失敗")
            return False

    except Exception as e:
        app.logger.error(f"{username}の投稿処理中にエラーが発生しました: {str(e)}", exc_info=True)
        log_activity(username, "ツイート投稿", f"失敗: {str(e)}")
        return False

def post_tweet():
    global current_status, next_post_time
    app.logger.info("一括ツイート投稿処理を開始します")
    update_status("一括ツイート処理中")
    
    active_accounts = get_active_accounts()
    if not active_accounts:
        app.logger.error("アクティブなTwitterアカウントがありません")
        update_status("エラー: アクティブなアカウントがありません")
        return False

    success_count = 0
    for account in active_accounts:
        if post_tweet_for_account(account):
            success_count += 1
        time.sleep(60)  # アカウント間に1分の待機時間を設ける

    app.logger.info(f"一括ツイート投稿処理が完了しました。成功: {success_count}/{len(active_accounts)}")
    
    update_status("待機中")
    if auto_posting_thread:
        next_post_time = datetime.now() + timedelta(seconds=auto_posting_interval)
    
    return True

def auto_post_tweet():
    global auto_posting_thread, next_post_time
    while auto_posting_thread:
        post_tweet()
        socketio.emit('status', {'message': '一括ツイートが完了しました'})
        time.sleep(auto_posting_interval)

@socketio.on('post_tweet')
def handle_post_tweet():
    success = post_tweet()
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