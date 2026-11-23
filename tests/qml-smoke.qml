import QtQuick
import Quickshell
import "components/switcherRank.js" as Rank

// Loads every QML file against the real Omarchy shell modules and reports any that fail to compile.
ShellRoot {
  Component.onCompleted: {
    var files = ["Signpost.qml", "components/HoverCard.qml", "components/ProjectSwitcher.qml",
                 "components/PinEditor.qml", "components/ProjectTile.qml", "components/SignIcon.qml",
                 "components/SignColors.qml", "components/Highlight.qml"]
    var failed = 0
    for (var i = 0; i < files.length; i++) {
      var component = Qt.createComponent(Qt.resolvedUrl(files[i]))
      if (component.status !== Component.Ready) {
        failed++
        console.log("QML_SMOKE_FAIL " + files[i] + ": " + component.errorString())
      }
    }
    var rows = [
      { id: "o1", name: "search", originalName: "search", detail: "~/Projects/search", workspace: 1, group: "open" },
      { id: "r1", name: "Arc · docs", originalName: "Arc · docs", detail: "~/Projects/Arc-wt-docs", workspace: 0, group: "recent" },
      { id: "p1", name: "Signpost", originalName: "Signpost", detail: "~/Projects/Signpost", workspace: 0, group: "projects" }
    ]
    var arcHit = Rank.filterProjects(rows, "arc", function() { return "" }, function(ws) { return String(ws) })
    var sgnpHit = Rank.filterProjects(rows, "sgnp", function() { return "" }, function(ws) { return String(ws) })
    if (!arcHit.length || arcHit[0].name !== "Arc · docs" || !sgnpHit.length || sgnpHit[0].name !== "Signpost") {
      failed++
      console.log("QML_SMOKE_FAIL components/switcherRank.js: ranking assertion failed")
    }
    console.log("QML_SMOKE_TOTAL " + files.length + " files, " + failed + " failed")
  }
}
