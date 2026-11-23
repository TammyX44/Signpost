import QtQuick
import QtQuick.Effects
import qs.Commons

// One small logo: a bundled agent logo, the app's icon from the icon theme,
// or a source glyph when neither exists (or iconStyle is "glyph").
Item {
  id: icon

  property var widget: null
  property var spec: null
  property string glyphKey: "app"
  property real size: widget ? widget.iconSize : 13
  property color tint: widget ? widget.fg : "white"
  readonly property string source: widget ? widget.iconSource(spec, glyphKey) : ""
  // The bundled Ollama and OpenAI logos are one light grey: drawn in the text colour instead, so they
  // read on light themes too. (Claude's is in its own colour; your overrides are left alone.)
  readonly property bool monochrome: !!spec && spec.kind === "agent" && (spec.key === "ollama" || spec.key === "openai")
                                     && source === String(Qt.resolvedUrl("../assets/icons/" + spec.key + ".svg"))

  implicitWidth: size
  implicitHeight: size

  Image {
    id: img
    anchors.fill: parent
    visible: icon.source !== "" && status !== Image.Error
    source: icon.source
    sourceSize.width: icon.size * 2
    sourceSize.height: icon.size * 2
    fillMode: Image.PreserveAspectFit
    smooth: true
    asynchronous: false  // small, cached: loading in place avoids a glyph flash on every change
    layer.enabled: icon.monochrome || (icon.widget !== null && icon.widget.iconStyle === "mono")
    layer.effect: MultiEffect { colorization: 1.0; colorizationColor: icon.tint }
  }

  Text {
    anchors.centerIn: parent
    visible: icon.source === "" || img.status === Image.Error
    text: icon.widget ? (icon.widget.glyphs[icon.glyphKey] || icon.widget.glyphs.app) : "•"
    textFormat: Text.PlainText
    color: icon.tint
    font.family: icon.widget && icon.widget.bar ? icon.widget.bar.fontFamily : Style.font.family
    font.pixelSize: icon.size
  }
}
