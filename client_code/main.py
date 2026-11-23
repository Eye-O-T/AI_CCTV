import os
import sys

from PyQt5.QtCore import QLibraryInfo
from PyQt5.QtWidgets import QApplication

from app_controller import AppController


def main():
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = QLibraryInfo.location(
        QLibraryInfo.PluginsPath
    )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    controller = AppController()
    controller.start()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
