document.addEventListener('DOMContentLoaded', function() {
    const addPostForm = document.getElementById('addPostForm');
    const editPostForm = document.getElementById('editPostForm');
    const postsTable = document.getElementById('postsTable').getElementsByTagName('tbody')[0];
    const editModal = document.getElementById('editModal');
    const closeModalBtn = document.getElementsByClassName('close')[0];

    function timeToSeconds(timeString) {
        if (!timeString) return '';
        const [minutes, seconds] = timeString.split(':').map(Number);
        return minutes * 60 + seconds;
    }

    function secondsToTime(seconds) {
        if (seconds === null || seconds === '') return '';
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
    }

    // 投稿一覧を取得して表示
    function fetchPosts() {
        fetch('/api/posts')
            .then(response => response.json())
            .then(posts => {
                postsTable.innerHTML = '';
                posts.forEach(post => {
                    const row = postsTable.insertRow();
                    row.innerHTML = `
                        <td>${post.filename}</td>
                        <td>${post.caption}</td>
                        <td>${post.reply_content}</td>
                        <td>${secondsToTime(post.start_time)}</td>
                        <td>${secondsToTime(post.end_time)}</td>
                        <td>
                            <button onclick="editPost(${post.id})">編集</button>
                            <button onclick="deletePost(${post.id})">削除</button>
                        </td>
                    `;
                });
            })
            .catch(error => console.error('Error:', error));
    }

    // 新規投稿の追加
    addPostForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const formData = new FormData(addPostForm);
        formData.set('start_time', timeToSeconds(formData.get('start_time')));
        formData.set('end_time', timeToSeconds(formData.get('end_time')));
        fetch('/api/posts', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            console.log('投稿が追加されました:', data);
            fetchPosts();
            addPostForm.reset();
        })
        .catch(error => console.error('Error:', error));
    });

    // 投稿の編集
    window.editPost = function(id) {
        fetch(`/api/posts/${id}`)
            .then(response => response.json())
            .then(post => {
                document.getElementById('editId').value = post.id;
                document.getElementById('editCaption').value = post.caption;
                document.getElementById('editReplyContent').value = post.reply_content;
                document.getElementById('editStartTime').value = secondsToTime(post.start_time);
                document.getElementById('editEndTime').value = secondsToTime(post.end_time);
                editModal.style.display = 'block';
            })
            .catch(error => console.error('Error:', error));
    }

    editPostForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const formData = new FormData(editPostForm);
        const id = document.getElementById('editId').value;
        formData.set('start_time', timeToSeconds(formData.get('start_time')));
        formData.set('end_time', timeToSeconds(formData.get('end_time')));
        fetch(`/api/posts/${id}`, {
            method: 'PUT',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            console.log('投稿が更新されました:', data);
            fetchPosts();
            editModal.style.display = 'none';
        })
        .catch(error => console.error('Error:', error));
    });

    // 投稿の削除
    window.deletePost = function(id) {
        if (confirm('本当にこの投稿を削除しますか？')) {
            fetch(`/api/posts/${id}`, {
                method: 'DELETE'
            })
            .then(response => response.json())
            .then(data => {
                console.log('投稿が削除されました:', data);
                fetchPosts();
            })
            .catch(error => console.error('Error:', error));
        }
    }

    // モーダルを閉じる
    closeModalBtn.onclick = function() {
        editModal.style.display = 'none';
    }

    window.onclick = function(event) {
        if (event.target == editModal) {
            editModal.style.display = 'none';
        }
    }

    // 初期表示時に投稿一覧を取得
    fetchPosts();
});