import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui

// Bar button for the agent's sandbox desktop.
//   left click   show / hide the desktop (starts it when stopped)
//   right click  stop it
// The icon lights up in the accent colour while the agent is acting.
BarWidget {
  id: root
  moduleName: "agentdesk"

  readonly property string bin: decodeURIComponent(
    Qt.resolvedUrl("../bin/agentdesk").toString().replace(/^file:\/\//, ""))
  readonly property string runtimeDir: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/agentdesk"
  readonly property string configPath:
    (Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config") + "/agentdesk/config.json"

  property bool running: false
  property bool shown: false
  property real lastActivity: 0
  property real now: Date.now() / 1000
  property color accent: "#ff7a1a"
  readonly property bool working: running && now - lastActivity < 6

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function refresh() {
    if (!statusProc.running) statusProc.running = true
  }

  function run(action) {
    Quickshell.execDetached(["python3", root.bin, action])
    refreshSoon.restart()
  }

  Process {
    id: statusProc
    command: ["python3", root.bin, "status", "--json"]
    stdout: StdioCollector { id: statusOut; waitForEnd: true }
    onExited: function(exitCode) {
      try {
        var status = JSON.parse(String(statusOut.text || "{}"))
        root.running = !!status.running
        root.shown = !!status.visible
      } catch (e) {
        root.running = false
      }
    }
  }

  FileView {
    path: root.runtimeDir + "/state.json"
    watchChanges: true
    printErrors: false
    onFileChanged: root.refresh()
    onLoaded: root.refresh()
    onLoadFailed: root.running = false
  }

  FileView {
    path: root.runtimeDir + "/activity.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.lastActivity = JSON.parse(text()).at || 0 } catch (e) {}
    }
  }

  FileView {
    path: root.configPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try {
        var accent = JSON.parse(text()).accent
        if (accent) root.accent = accent
      } catch (e) {}
    }
  }

  Timer {
    interval: 1000
    repeat: true
    running: root.running
    onTriggered: root.now = Date.now() / 1000
  }

  Timer {
    interval: 5000
    repeat: true
    running: root.running
    onTriggered: root.refresh()
  }

  Timer {
    id: refreshSoon
    interval: 1200
    onTriggered: root.refresh()
  }

  Component.onCompleted: refresh()

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: String.fromCodePoint(root.working ? 0xF0CFD : 0xF01C0)
    active: root.running && (root.working || root.shown)
    activeColor: root.accent
    dimmed: !root.running
    tooltipText: !root.running ? "Agent desktop stopped · click to start"
      : root.working ? "Agent is working · click to " + (root.shown ? "hide" : "watch")
      : "Agent desktop " + (root.shown ? "visible" : "hidden") + " · right-click to stop"
    onPressed: function(b) {
      if (b === Qt.RightButton) root.run("stop")
      else root.run("toggle")
    }

    SequentialAnimation on opacity {
      running: root.working
      loops: Animation.Infinite
      alwaysRunToEnd: true
      NumberAnimation { to: 0.45; duration: 600; easing.type: Easing.InOutSine }
      NumberAnimation { to: 1.0; duration: 600; easing.type: Easing.InOutSine }
    }
  }
}
