document.addEventListener('DOMContentLoaded', function() {
    const addAccountForm = document.getElementById('addAccountForm');
    const editAccountForm = document.getElementById('editAccountForm');
    const accountsTable = document.getElementById('accountsTable').getElementsByTagName('tbody')[0];
    const editModal = document.getElementById('editModal');
    const closeModalBtn = document.getElementsByClassName('close')[0];

    // アカウント一覧を取得して表示
    function fetchAccounts() {
        fetch('/api/accounts')
            .then(response => response.json())
            .then(accounts => {
                accountsTable.innerHTML = '';
                accounts.forEach(account => {
                    const row = accountsTable.insertRow();
                    row.innerHTML = `
                        <td>${account.username}</td>
                        <td>${account.post_flag ? '有効' : '無効'}</td>
                        <td>
                            <button onclick="editAccount(${account.id})">編集</button>
                            <button onclick="deleteAccount(${account.id})">削除</button>
                        </td>
                    `;
                });
            })
            .catch(error => console.error('Error:', error));
    }

    // 新規アカウントの追加
    addAccountForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const formData = new FormData(addAccountForm);
        const accountData = {
            username: formData.get('username'),
            consumer_key: formData.get('consumer_key'),
            consumer_secret: formData.get('consumer_secret'),
            access_token: formData.get('access_token'),
            access_token_secret: formData.get('access_token_secret'),
            post_flag: formData.get('post_flag') ? 1 : 0
        };
        fetch('/api/accounts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(accountData)
        })
        .then(response => response.json())
        .then(data => {
            console.log('アカウントが追加されました:', data);
            fetchAccounts();
            addAccountForm.reset();
        })
        .catch(error => console.error('Error:', error));
    });

    // アカウントの編集
    window.editAccount = function(id) {
        fetch(`/api/accounts/${id}`)
            .then(response => response.json())
            .then(account => {
                document.getElementById('editId').value = account.id;
                document.getElementById('editUsername').value = account.username;
                document.getElementById('editPostFlag').checked = account.post_flag;
                editModal.style.display = 'block';
            })
            .catch(error => console.error('Error:', error));
    }

    editAccountForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const formData = new FormData(editAccountForm);
        const accountData = {
            username: formData.get('username'),
            consumer_key: formData.get('consumer_key'),
            consumer_secret: formData.get('consumer_secret'),
            access_token: formData.get('access_token'),
            access_token_secret: formData.get('access_token_secret'),
            post_flag: formData.get('post_flag') ? 1 : 0
        };
        const id = document.getElementById('editId').value;
        fetch(`/api/accounts/${id}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(accountData)
        })
        .then(response => response.json())
        .then(data => {
            console.log('アカウントが更新されました:', data);
            fetchAccounts();
            editModal.style.display = 'none';
        })
        .catch(error => console.error('Error:', error));
    });

    // アカウントの削除
    window.deleteAccount = function(id) {
        if (confirm('本当にこのアカウントを削除しますか？')) {
            fetch(`/api/accounts/${id}`, {
                method: 'DELETE'
            })
            .then(response => response.json())
            .then(data => {
                console.log('アカウントが削除されました:', data);
                fetchAccounts();
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

    // 初期表示時にアカウント一覧を取得
    fetchAccounts();
});