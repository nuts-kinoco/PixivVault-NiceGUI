def _author_page_diff_dl(_ev=None):
    author_id = _author_page_state["author_id"]
    if not author_id:
        return
    def _work():
        try:
            run_backup(author_id, _get_client(), db, new_only=True)
            _marshal_call(lambda: _show_result_snack("差分DLを実行しました。", True))
        except Exception:
            logger.exception(f"作者ページ: 差分DLに失敗しました (author_id={author_id})")
            _marshal_call(lambda: _show_result_snack("差分DLに失敗しました。", False))
    threading.Thread(target=_work, daemon=True).start()
