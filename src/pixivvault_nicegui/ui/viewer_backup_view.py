"""Original author backup history or recent managed individual exports."""
import asyncio
from nicegui import ui, background_tasks
from .card_size_control import size_buttons,update_size_buttons
from .grid_layout import MAX_COLUMNS


class ViewerBackupView:
    def __init__(self,services,*,card_size='large',on_size_change=None):
        self.services=services;self.client=ui.context.client
        self.active=False;self.closed=False;self.busy=False;self.epoch=0
        self.card_size=card_size;self.on_size_change=on_size_change;self.rows=();self.openers={}
        self.legacy_mode=bool(getattr(services,'legacy_backup_history_available',False))
        self._images=None
        with ui.column().classes('pv-gallery w-full').props('data-testid=backup-history') as self.root:
            with ui.row().classes('pv-toolbar'):
                ui.label('直近のバックアップ').classes('pv-library-heading')
                self.size_buttons=size_buttons(self.change_size,prefix='backup')
                self.refresh_button=ui.button('更新',on_click=self.refresh).props('flat data-testid=backup-refresh')
            ui.label('旧ライブラリのバックアップ日時順・最大60作者。画像を押すと保存先を開きます。'
                     if self.legacy_mode else
                     '個別DLで保存した作品の履歴です。作者ごとに直近の保存を表示します。').classes('pv-subtitle')
            self.status=ui.label('').props('role=status data-testid=backup-status')
            with ui.element('div').classes('pv-gallery-scroll'):
                self.grid=ui.element('div').classes('pv-grid').props('data-testid=backup-grid')
        self.apply_card_size(card_size)

    def change_size(self,size):
        if self.closed or not self.active:return
        if self.on_size_change:self.on_size_change(size)
        else:self.apply_card_size(size)

    def apply_card_size(self,size):
        if size not in MAX_COLUMNS:raise ValueError('invalid card size')
        if self.closed:return
        self.card_size=size;self.grid.style(f'--pv-max-cols:{MAX_COLUMNS[size]}')
        update_size_buttons(self.size_buttons,size)

    async def refresh(self):
        if self.closed or not self.active or self.busy:return
        epoch=self.epoch;self.busy=True;self.refresh_button.disable()
        incoming_images=None
        try:
            if self.legacy_mode:
                rows,errors,incoming_images=await self.services.recent_legacy_backup_authors()
            else:
                rows,errors=await self.services.recent_viewer_backups()
            if self.closed or not self.active or epoch!=self.epoch:return
            self.rows=rows;self.grid.clear();self.openers={}
            with self.grid:
                for row in rows:
                    if self.legacy_mode:
                        with ui.element('div').classes('pv-card').props(
                                'data-testid=backup-author-'+row.author_id):
                            # The gallery card is a flex column. Keep the
                            # thumbnail flexible and the caption in a fixed
                            # metadata block so the image never hides labels.
                            opener=ui.element('div').classes('flex flex-col w-full h-full min-h-0 gap-1 cursor-pointer').props(
                                'role=button data-testid=backup-open-'+row.author_id)
                            if getattr(self.services,'desktop',None) is None:
                                opener.props('aria-disabled=true tabindex=-1')
                            else:
                                opener.props('tabindex=0 aria-label=作者の保存先を開く')
                                async def open_legacy_event(_,selected=row,captured=epoch):
                                    await self.open_legacy(selected,captured)
                                opener.on('click',open_legacy_event)
                                opener.on('keydown.enter',open_legacy_event)
                                opener.on('keydown.space',open_legacy_event)
                            with opener:
                                ui.image(row.thumbnail).classes('pv-thumb').props('fit=cover loading=lazy no-transition')
                                with ui.element('div').classes('pv-card-meta'):
                                    ui.label(row.author_name).classes('pv-card-author')
                                    ui.label(row.title).classes('pv-card-title')
                                    ui.label(row.last_backup).classes('pv-subtitle')
                            self.openers[row.author_id]=opener
                    else:
                        with ui.column().classes('pv-card').props('data-testid=backup-author-'+row['author_id']):
                            ui.image(row['thumbnail']).classes('pv-thumb')
                            ui.label(row['author_name'] or row['author_id']).classes('pv-card-author')
                            ui.label(row['title']).classes('pv-card-title')
                            ui.label(row['saved_at']).classes('pv-subtitle')
                            button=ui.button('保存先を開く').props('flat dense data-testid=backup-open-'+row['author_id'])
                            async def open_event(_,manifest=row['manifest'],captured=epoch):
                                await self.open_saved(manifest,captured)
                            button.on('click',open_event)
                            button.set_enabled(getattr(self.services,'desktop',None) is not None)
                            self.openers[row['author_id']]=button
            if self.legacy_mode:
                previous=self._images;self._images=incoming_images;incoming_images=None
                if previous is not None:previous.close()
                self.status.set_text(
                    f'{len(rows)} 作者' + (f'・画像を確認できない作者 {errors} 件' if errors else '')
                    if rows or errors else 'バックアップ日時が登録された作者はいません。')
            else:
                self.status.set_text(f'{len(rows)} 作者' + (f'・確認できない履歴 {len(errors)} 件' if errors else ''))
        except Exception:
            if not self.closed and self.active and epoch==self.epoch:self.status.set_text('履歴を読み込めませんでした。更新で再試行してください。')
        finally:
            if incoming_images is not None:incoming_images.close()
            self.busy=False
            if not self.closed:
                self.refresh_button.enable()
                if self.active and epoch!=self.epoch:background_tasks.create(self.refresh())

    async def open_saved(self,manifest,epoch):
        if self.closed or not self.active or epoch!=self.epoch or not any(r['manifest']==manifest for r in self.rows):return
        try:await self.services.open_viewer_backup(manifest)
        except Exception:
            if not self.closed and self.active and epoch==self.epoch:self.status.set_text('保存先を開けませんでした。保存先と外部ビューアの設定を確認してください。')

    async def open_legacy(self,row,epoch):
        if (self.closed or not self.active or epoch!=self.epoch
                or row not in self.rows or self._images is None):return
        try:await self.services.open_legacy_backup_author(row,self._images)
        except Exception:
            if not self.closed and self.active and epoch==self.epoch:
                self.status.set_text('作者の保存先を開けませんでした。フォルダ・ZIPの状態を確認してください。')

    def set_active(self,active):
        if self.closed:return
        changed=self.active!=bool(active);self.active=bool(active)
        if changed:self.epoch+=1
        if not self.active and self._images is not None:
            self._images.close();self._images=None
        if self.active and changed:background_tasks.create(self.refresh())

    def close(self):
        self.closed=True;self.active=False;self.epoch+=1
        if self._images is not None:self._images.close();self._images=None
