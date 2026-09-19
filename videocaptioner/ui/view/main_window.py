import atexit
import os
import shutil
import sys

import psutil
from PyQt5.QtCore import QRect, QSize, Qt, QThread, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices, QIcon
from PyQt5.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    NavigationItemPosition,
    SplashScreen,
)

from videocaptioner.config import ASSETS_PATH, GITHUB_REPO_URL
from videocaptioner.core.constant import INFOBAR_DURATION_FOREVER
from videocaptioner.ui.common.config import cfg
from videocaptioner.ui.components.DonateDialog import DonateDialog
from videocaptioner.ui.thread.version_checker_thread import VersionChecker
from videocaptioner.ui.view.batch_process_interface import BatchProcessInterface
from videocaptioner.ui.view.home_interface import HomeInterface
from videocaptioner.ui.view.llm_logs_interface import LLMLogsInterface
from videocaptioner.ui.view.setting_interface import SettingInterface
from videocaptioner.ui.view.subtitle_style_interface import SubtitleStyleInterface

LOGO_PATH = ASSETS_PATH / "logo.png"


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.initWindow()

        # 创建子界面
        self.homeInterface = HomeInterface(self)
        self.settingInterface = SettingInterface(self)
        self.subtitleStyleInterface = SubtitleStyleInterface(self)
        self.batchProcessInterface = BatchProcessInterface(self)
        self.llmLogsInterface = LLMLogsInterface(self)

        # 初始化版本检查器
        self.versionChecker = VersionChecker()
        self.versionChecker.newVersionAvailable.connect(self.onNewVersion)
        self.versionChecker.announcementAvailable.connect(self.onAnnouncement)

        self.versionThread = QThread()
        self.versionChecker.moveToThread(self.versionThread)
        self.versionThread.started.connect(self.versionChecker.perform_check)
        self.versionThread.start()

        # 初始化导航界面
        self.initNavigation()
        self.splashScreen.finish()

        # 检查系统依赖
        self._check_ffmpeg()

        # 注册退出处理， 清理进程
        atexit.register(self.stop)

    def initNavigation(self):
        """初始化导航栏"""
        # 添加导航项
        self.addSubInterface(self.homeInterface, FIF.HOME, self.tr("主页"))
        self.addSubInterface(self.batchProcessInterface, FIF.VIDEO, self.tr("批量处理"))
        self.addSubInterface(self.subtitleStyleInterface, FIF.FONT, self.tr("字幕样式"))
        self.addSubInterface(self.llmLogsInterface, FIF.HISTORY, self.tr("请求日志"))

        self.navigationInterface.addSeparator()

        # 在底部添加自定义小部件
        self.navigationInterface.addItem(
            routeKey="avatar",
            text="GitHub",
            icon=FIF.GITHUB,
            onClick=self.onGithubDialog,
            position=NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            self.settingInterface,
            FIF.SETTING,
            self.tr("Settings"),
            NavigationItemPosition.BOTTOM,
        )

        # 设置默认界面
        self.switchTo(self.homeInterface)

    def switchTo(self, interface):
        if interface.windowTitle():
            self.setWindowTitle(interface.windowTitle())
        else:
            self.setWindowTitle(self.tr("卡卡字幕助手 -- VideoCaptioner"))
        self.stackedWidget.setCurrentWidget(interface, popOut=False)

    def initWindow(self):
        """初始化窗口"""
        self.resize(1050, 800)
        self.setMinimumWidth(700)
        self.setWindowIcon(QIcon(str(LOGO_PATH)))
        self.setWindowTitle(self.tr("卡卡字幕助手 -- VideoCaptioner"))

        if sys.platform == "darwin":
            self._init_mac_title_bar()

        self.setMicaEffectEnabled(cfg.get(cfg.micaEnabled))

        # 创建启动画面
        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()

        # 设置窗口位置, 居中
        desktop = QApplication.desktop().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)

        self.show()
        QApplication.processEvents()
        if sys.platform == "darwin":
            self._position_mac_return_button()
            QTimer.singleShot(0, self._position_mac_return_button)

    def _init_mac_title_bar(self) -> None:
        """macOS adjustments for the Fluent window chrome.

        - Show the native traffic lights at the top-left (qfluentwidgets
          draws Windows-style caption buttons at the top-right and keeps the
          native ones hidden), inset like Finder windows.
        - Move the navigation panel's return button into its own row at the
          top of the rail icon column, below the traffic lights.
        - Center the window icon and title in the title bar.

        The hardcoded metrics below (78pt top margin, 42pt return-row top)
        are tied to the pinned qfluentwidgets 1.8.4 layout: the title bar is
        48pt high and the rail rail buttons are 40x36pt. Revisit them when
        bumping qfluentwidgets.
        """
        self.setSystemTitleBarButtonVisible(True)
        self.titleBar.minBtn.hide()
        self.titleBar.maxBtn.hide()
        self.titleBar.closeBtn.hide()

        title_bar = self.titleBar
        title_bar.hBoxLayout.removeWidget(title_bar.iconLabel)
        title_bar.hBoxLayout.removeWidget(title_bar.titleLabel)
        title_bar.hBoxLayout.insertStretch(0, 1)
        title_bar.hBoxLayout.insertWidget(1, title_bar.iconLabel, 0, Qt.AlignVCenter)
        title_bar.hBoxLayout.insertWidget(2, title_bar.titleLabel, 0, Qt.AlignVCenter)
        title_bar.hBoxLayout.insertStretch(3, 1)
        # qframelesswindow's TitleBar base leaves a stretch of its own before
        # the caption buttons, so the row ends up with one leading and two
        # trailing spacers and the icon+title sit left of center. Keep only
        # the leading spacer and one trailing spacer.
        spacer_indexes = [
            i for i in range(title_bar.hBoxLayout.count())
            if title_bar.hBoxLayout.itemAt(i).spacerItem() is not None
        ]
        for i in reversed(spacer_indexes[2:]):
            title_bar.hBoxLayout.takeAt(i)

        panel = self.navigationInterface.panel
        return_button = panel.returnButton
        panel.topLayout.removeWidget(return_button)
        # Push the menu button (and the items under it) below the return row.
        panel.topLayout.setContentsMargins(4, 78, 4, 0)
        return_button.setParent(panel)
        return_button.setFixedSize(40, 36)
        return_button.show()

    def _position_mac_return_button(self) -> None:
        """Center the return button on the nav rail's icon column axis.

        The axis is read from the laid-out menu button rather than hardcoded,
        so it stays aligned across DPI scales and layout revisions.
        """
        panel = self.navigationInterface.panel
        menu_geometry = panel.menuButton.geometry()
        return_button = panel.returnButton
        return_button.setGeometry(
            menu_geometry.x() + (menu_geometry.width() - return_button.width()) // 2,
            42,
            return_button.width(),
            return_button.height(),
        )

    def systemTitleBarRect(self, size: QSize) -> QRect:
        """Place the native traffic lights Finder-style.

        Metrics measured in points via AppKit/Accessibility (scale
        independent): buttons are 14x16pt with 20pt center spacing. The
        button container is the standard 28pt title bar, but it does not
        clip its subviews, so the buttons are placed at Finder's vertical
        position (top edge 18pt from the window top, center 26pt) and
        Finder's horizontal inset (close origin x=18, center 25).
        """
        if sys.platform == "darwin":
            return QRect(8, 26 - size.height() // 2, 74, size.height())
        return super().systemTitleBarRect(size)

    def onGithubDialog(self):
        """打开GitHub"""
        w = MessageBox(
            self.tr("GitHub信息"),
            self.tr(
                "VideoCaptioner 由本人在课余时间独立开发完成，目前托管在GitHub上，欢迎Star和Fork。项目诚然还有很多地方需要完善，遇到软件的问题或者BUG欢迎提交Issue。\n\n https://github.com/WEIFENG2333/VideoCaptioner"
            ),
            self,
        )
        w.yesButton.setText(self.tr("打开 GitHub"))
        w.cancelButton.setText(self.tr("支持作者"))
        if w.exec():
            QDesktopServices.openUrl(QUrl(GITHUB_REPO_URL))
        else:
            # 点击"支持作者"按钮时打开捐赠对话框
            donate_dialog = DonateDialog(self)
            donate_dialog.exec_()

    def onNewVersion(self, version, update_required, update_info, download_url):
        """新版本提示"""
        if update_required:
            title = "发现新版本, 需要更新"
            content = f"发现新版本 {version}\n\n" f"更新内容：\n{update_info}"
        else:
            title = "发现新版本"
            content = f"发现新版本 {version}\n\n{update_info}"

        w = MessageBox(title, content, self)
        w.yesButton.setText("立即更新")
        w.cancelButton.setText("稍后再说")

        if w.exec() or update_required:
            QDesktopServices.openUrl(QUrl(download_url))

        if update_required:
            self.homeInterface.setEnabled(False)
            self.batchProcessInterface.setEnabled(False)
            InfoBar.error(
                title="需要更新",
                content=self.tr("当前版本部分功能已被禁用。请尽快更新。"),
                isClosable=False,
                position=InfoBarPosition.BOTTOM,
                duration=-1,
                parent=self,
            )

    def onAnnouncement(self, content):
        """显示公告"""
        w = MessageBox("公告", content, self)
        w.yesButton.setText("我知道了")
        w.cancelButton.hide()
        w.exec()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "splashScreen"):
            self.splashScreen.resize(self.size())
        if sys.platform == "darwin":
            # FluentWindow offsets the title bar by its fixed 46pt Windows
            # nav rail; on macOS the traffic lights and the centered title
            # belong to the full-width window chrome.
            self.titleBar.move(0, 0)
            self.titleBar.resize(self.width(), self.titleBar.height())

    def closeEvent(self, event):
        # 关闭所有子界面
        # self.homeInterface.close()
        # self.batchProcessInterface.close()
        # self.subtitleStyleInterface.close()
        # self.settingInterface.close()
        super().closeEvent(event)

        # 强制退出应用程序
        QApplication.quit()

        # 确保所有线程和进程都被终止 要是一些错误退出就不会处理了。
        # import os
        # os._exit(0)

    def stop(self):
        # 找到 FFmpeg 进程并关闭
        process = psutil.Process(os.getpid())
        for child in process.children(recursive=True):
            child.kill()

    def _check_ffmpeg(self):
        """检查 FFmpeg 是否已安装"""
        if shutil.which("ffmpeg") is None:
            InfoBar.warning(
                self.tr("FFmpeg 未安装"),
                self.tr("软件处理音视频文件时需要 FFmpeg，请先安装"),
                duration=INFOBAR_DURATION_FOREVER,
                position=InfoBarPosition.BOTTOM,
                parent=self,
            )
