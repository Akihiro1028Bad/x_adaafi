document.addEventListener('DOMContentLoaded', function() {
    const socket = io({
        transports: ['websocket'],
        upgrade: false
    });

    const postTweetButton = document.getElementById('postTweet');
    const startAutoPostButton = document.getElementById('startAutoPost');
    const stopAutoPostButton = document.getElementById('stopAutoPost');
    const postIntervalInput = document.getElementById('postInterval');
    const currentStatusElement = document.getElementById('currentStatus');
    const autoPostStatusElement = document.getElementById('autoPostStatus');
    const recentActivitiesElement = document.getElementById('recentActivities');
    const toggleThemeButton = document.getElementById('toggleTheme');

    let autoPostingActive = false;

    socket.on('connect', function() {
        console.log('Socket.IOに接続しました');
        updateStatus('サーバーに接続しました');
    });

    socket.on('disconnect', function() {
        console.log('Socket.IOから切断されました');
        updateStatus('サーバーから切断されました');
    });

    postTweetButton.addEventListener('click', function() {
        console.log('ツイート投稿ボタンがクリックされました');
        socket.emit('post_tweet');
        updateStatus('ツイートを投稿中...');
    });

    startAutoPostButton.addEventListener('click', function() {
        const interval = parseInt(postIntervalInput.value);
        if (interval > 0) {
            console.log('自動投稿を開始します。間隔:', interval, '分');
            socket.emit('start_auto_posting', { interval: interval });
            updateStatus('自動投稿を開始しています...');
            autoPostingActive = true;
            updateAutoPostStatus();
        } else {
            console.error('無効な間隔です');
            updateStatus('無効な間隔です。正の整数を入力してください。');
        }
    });

    stopAutoPostButton.addEventListener('click', function() {
        console.log('自動投稿を停止します');
        socket.emit('stop_auto_posting');
        updateStatus('自動投稿を停止しています...');
        autoPostingActive = false;
        updateAutoPostStatus();
    });

    socket.on('status', function(data) {
        console.log('ステータスを受信:', data);
        updateStatus(data.message);
    });

    socket.on('status_update', function(data) {
        console.log('ステータス更新を受信:', data);
        updateStatus(data.status);
    });

    socket.on('app_status', function(data) {
        updateAppStatus(data);
    });

    socket.on('connect_error', (error) => {
        console.error('接続エラー:', error);
        updateStatus('接続エラーが発生しました');
    });

    function updateStatus(message) {
        currentStatusElement.textContent = `現在の状態: ${message}`;
    }

    function updateAppStatus(data) {
        updateStatus(data.current_status);
        autoPostingActive = data.auto_posting_active;
        updateAutoPostStatus(data.auto_posting_interval, data.next_post_time);
        updateRecentActivities(data.recent_activities);
    }

    function updateAutoPostStatus(interval, nextPostTime) {
        if (autoPostingActive) {
            const nextPostTimeStr = nextPostTime ? formatDateTime(nextPostTime) : '計算中...';
            autoPostStatusElement.textContent = `自動投稿中 (間隔: ${interval}分, 次の投稿: ${nextPostTimeStr})`;
            startAutoPostButton.disabled = true;
            stopAutoPostButton.disabled = false;
        } else {
            autoPostStatusElement.textContent = '自動投稿は停止中です';
            startAutoPostButton.disabled = false;
            stopAutoPostButton.disabled = true;
        }
    }

    function updateRecentActivities(activities) {
        recentActivitiesElement.innerHTML = '';
        activities.forEach(activity => {
            const li = document.createElement('li');
            li.textContent = `${formatDateTime(activity.timestamp)} - ${activity.account}: ${activity.action} (${activity.result})`;
            recentActivitiesElement.appendChild(li);
        });
    }

    function formatDateTime(isoString) {
        if (!isoString) return 'N/A';
        const date = new Date(isoString);
        return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
    }

    toggleThemeButton.addEventListener('click', function() {
        document.body.classList.toggle('dark-mode');
        const icon = this.querySelector('i');
        if (document.body.classList.contains('dark-mode')) {
            icon.classList.remove('fa-moon');
            icon.classList.add('fa-sun');
        } else {
            icon.classList.remove('fa-sun');
            icon.classList.add('fa-moon');
        }
    });

    // 初期状態を取得
    socket.emit('get_app_status');
});