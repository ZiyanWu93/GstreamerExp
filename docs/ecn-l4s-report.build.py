#!/usr/bin/env python3
# Build the ECN/L4S research report PDF with reportlab.
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, KeepTogether, Flowable)
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon, Group
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

# Embed Arial (metric-compatible with Helvetica) under the Helvetica names so every
# viewer AND poppler render the text. base-14 Helvetica was rendering blank here.
_F = "/System/Library/Fonts/Supplemental/"
def _reg(name, fn, fb=None):
    p = _F + fn
    if not os.path.exists(p) and fb: p = _F + fb
    pdfmetrics.registerFont(TTFont(name, p))
_reg("Helvetica", "Arial.ttf")
_reg("Helvetica-Bold", "Arial Bold.ttf")
_reg("Helvetica-Oblique", "Arial Italic.ttf", "Arial.ttf")
_reg("Helvetica-BoldOblique", "Arial Bold Italic.ttf", "Arial Bold.ttf")
pdfmetrics.registerFontFamily("Helvetica", normal="Helvetica", bold="Helvetica-Bold",
                              italic="Helvetica-Oblique", boldItalic="Helvetica-BoldOblique")

OUT = "/Users/ziyan/project/writing/projects/GstreamerExp/docs/ecn-l4s-report.pdf"

# ---------- palette ----------
INK     = colors.HexColor("#1a2330")
ACCENT  = colors.HexColor("#1f4e79")   # deep blue
ACCENT2 = colors.HexColor("#2e7d6f")   # teal
RED     = colors.HexColor("#b3261e")
AMBER   = colors.HexColor("#9a6700")
LGRAY   = colors.HexColor("#eef1f5")
MGRAY   = colors.HexColor("#d6dbe2")
CODEBG  = colors.HexColor("#f3f4f6")
GREEN   = colors.HexColor("#1f7a3f")

# ---------- styles ----------
ss = getSampleStyleSheet()
def S(name, **kw):
    base = kw.pop("parent", ss["Normal"])
    return ParagraphStyle(name, parent=base, **kw)

title_s   = S("title_s", fontName="Helvetica-Bold", fontSize=21, leading=25, textColor=INK, spaceAfter=4)
sub_s     = S("sub_s", fontName="Helvetica", fontSize=11, leading=15, textColor=ACCENT, spaceAfter=2)
meta_s    = S("meta_s", fontName="Helvetica", fontSize=8.5, leading=12, textColor=colors.HexColor("#5b6573"))
h1_s      = S("h1_s", fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=ACCENT, spaceBefore=14, spaceAfter=6)
h2_s      = S("h2_s", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=INK, spaceBefore=9, spaceAfter=3)
body_s    = S("body_s", fontName="Helvetica", fontSize=9.6, leading=14, textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6)
bull_s    = S("bull_s", parent=body_s, leftIndent=14, bulletIndent=3, spaceAfter=3, alignment=TA_LEFT)
code_s    = S("code_s", fontName="Courier", fontSize=8.2, leading=11, textColor=INK)
cap_s     = S("cap_s", fontName="Helvetica-Oblique", fontSize=8, leading=11, textColor=colors.HexColor("#5b6573"), alignment=TA_CENTER, spaceBefore=2, spaceAfter=8)
callout_s = S("callout_s", parent=body_s, fontSize=9.6, leading=14, spaceAfter=0)
cell_s    = S("cell_s", fontName="Helvetica", fontSize=8.6, leading=11.5, textColor=INK)
cellb_s   = S("cellb_s", parent=cell_s, fontName="Helvetica-Bold")
cellw_s   = S("cellw_s", parent=cell_s, textColor=colors.white, fontName="Helvetica-Bold")

def P(t, s=body_s): return Paragraph(t, s)
def B(t): return Paragraph(("&bull;&nbsp; " + t), bull_s)

def callout(title, body, bg=LGRAY, bar=ACCENT):
    inner = [Paragraph("<b>%s</b>" % title, S("ct", parent=callout_s, textColor=bar, spaceAfter=2))]
    if body: inner.append(Paragraph(body, callout_s))
    t = Table([[inner]], colWidths=[6.7*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),bg),
        ("LINEBEFORE",(0,0),(0,-1),3,bar),
        ("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),
        ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
    ]))
    return t

def codebox(lines):
    flow = [Paragraph(l.replace(" ","&nbsp;").replace("<","&lt;").replace(">","&gt;"), code_s) for l in lines]
    t = Table([[flow]], colWidths=[6.7*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),CODEBG),("BOX",(0,0),(-1,-1),0.5,MGRAY),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    return t

def tbl(data, widths, header=True, font=8.6, align_first_left=True):
    rows = []
    for r, row in enumerate(data):
        cells = []
        for c, val in enumerate(row):
            st = cellw_s if (header and r==0) else (cellb_s if (c==0 and align_first_left) else cell_s)
            cells.append(Paragraph(str(val), st))
        rows.append(cells)
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("GRID",(0,0),(-1,-1),0.4,MGRAY),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, colors.HexColor("#f7f9fb")]),
    ]
    if header:
        style += [("BACKGROUND",(0,0),(-1,0),ACCENT)]
    t.setStyle(TableStyle(style))
    return t

# ---------- diagram helpers ----------
def box(g, x, y, w, h, text, fill, stroke=INK, tcol=colors.white, fs=8, bold=True):
    g.add(Rect(x, y, w, h, fillColor=fill, strokeColor=stroke, strokeWidth=0.8, rx=3, ry=3))
    fn = "Helvetica-Bold" if bold else "Helvetica"
    lines = text.split("\n")
    n = len(lines)
    for i, ln in enumerate(lines):
        g.add(String(x+w/2, y+h/2 + (n-1)*(fs*0.6) - i*(fs*1.15), ln,
                     fontName=fn, fontSize=fs, fillColor=tcol, textAnchor="middle"))

def arrow(g, x1, y1, x2, y2, col=INK, label=None, lcol=None, w=1.0, dash=False):
    ln = Line(x1, y1, x2, y2, strokeColor=col, strokeWidth=w)
    if dash: ln.strokeDashArray=[3,2]
    g.add(ln)
    import math
    ang = math.atan2(y2-y1, x2-x1); ah=5
    g.add(Polygon(points=[x2,y2,
        x2-ah*math.cos(ang-0.4), y2-ah*math.sin(ang-0.4),
        x2-ah*math.cos(ang+0.4), y2-ah*math.sin(ang+0.4)],
        fillColor=col, strokeColor=col))
    if label:
        g.add(String((x1+x2)/2, max(y1,y2)+4, label, fontName="Helvetica", fontSize=7,
                     fillColor=lcol or col, textAnchor="middle"))

def diag_5gpath():
    g = Drawing(470, 168)
    g.add(String(235,158,"Figure 2 — Where ECN-CE marking happens in the 5G path (and the uplink case)",
                 fontName="Helvetica-Bold", fontSize=8.5, fillColor=INK, textAnchor="middle"))
    yb=92; w=78; h=34
    xs=[6,118,230,342]
    box(g, xs[0], yb, w, h, "UE\n(SCReAM Tx,\nuplink media)", ACCENT2, fs=7.2)
    box(g, xs[1], yb, w, h, "gNB / RAN\n(radio queue)", ACCENT, fs=7.6)
    box(g, xs[2], yb, w, h, "UPF\n(core)", ACCENT, fs=7.6)
    box(g, xs[3], yb, w+38, h, "Internet  ->  Media server\n(SCReAM Rx)", INK, fs=7.2)
    # uplink arrows
    arrow(g, xs[0]+w, yb+h*0.65, xs[1], yb+h*0.65, col=RED, w=1.6)
    arrow(g, xs[1]+w, yb+h*0.65, xs[2], yb+h*0.65, col=INK)
    arrow(g, xs[2]+w, yb+h*0.65, xs[3], yb+h*0.65, col=INK)
    # bottleneck flag
    g.add(String(xs[0]+w+ (xs[1]-(xs[0]+w))/2, yb+h+6, "bottleneck", fontName="Helvetica-Oblique", fontSize=7, fillColor=RED, textAnchor="middle"))
    # marking-point notes
    g.add(String(xs[1]+w/2, yb-10, "<-- marks here (3GPP Rel-18)", fontName="Helvetica", fontSize=6.6, fillColor=AMBER, textAnchor="middle"))
    g.add(String(xs[2]+w/2, yb-10, "<-- or here (Rel-18)", fontName="Helvetica", fontSize=6.6, fillColor=AMBER, textAnchor="middle"))
    # feedback arrow (RTCP)
    arrow(g, xs[3]+10, yb, xs[0]+10, yb, col=ACCENT2, w=1.0, dash=True)
    g.add(String(235, yb-22, "RTCP feedback echoes the 2-bit ECN back to the sender (RFC 8888)",
                 fontName="Helvetica-Oblique", fontSize=7, fillColor=ACCENT2, textAnchor="middle"))
    # status line
    g.add(String(235, yb-40, "srsRAN / OAI (mainline): no marking, ECN field passed through.  L4Span (research): marks here, UL+DL.",
                 fontName="Helvetica", fontSize=7, fillColor=GREEN, textAnchor="middle"))
    return g

def diag_dualq():
    g = Drawing(470, 150)
    g.add(String(235,140,"Figure 3 — L4S DualQ: not 'marked traffic first', but early CE-mark instead of drop",
                 fontName="Helvetica-Bold", fontSize=8.5, fillColor=INK, textAnchor="middle"))
    # classic queue
    box(g, 30, 84, 150, 30, "Classic queue  (Not-ECT / ECT(0))", colors.HexColor("#7a8696"), fs=7.6)
    g.add(String(105, 74, "AQM DROPS when delay rises", fontName="Helvetica", fontSize=7, fillColor=RED, textAnchor="middle"))
    # L4S queue
    box(g, 30, 36, 150, 30, "L4S queue  (ECT(1))", ACCENT2, fs=7.6)
    g.add(String(105, 26, "AQM CE-MARKS early + often (no drop)", fontName="Helvetica", fontSize=7, fillColor=GREEN, textAnchor="middle"))
    # coupling + scheduler
    box(g, 210, 60, 96, 40, "Coupled AQM\n+ scheduler\n(conditional priority)", AMBER, fs=7.2)
    arrow(g, 180, 99, 210, 90, col=INK)
    arrow(g, 180, 51, 210, 70, col=INK)
    arrow(g, 306, 80, 360, 80, col=INK)
    box(g, 360, 64, 96, 32, "Link\n(shared rate)", INK, fs=7.4)
    g.add(String(258, 50, "couples drop(classic) to mark(L4S) for fairness", fontName="Helvetica-Oblique", fontSize=6.6, fillColor=AMBER, textAnchor="middle"))
    return g

def diag_loop():
    g = Drawing(470, 120)
    g.add(String(235,110,"Figure 1 — The SCReAM ECN control loop",
                 fontName="Helvetica-Bold", fontSize=8.5, fillColor=INK, textAnchor="middle"))
    yb=52; h=34
    box(g, 8, yb, 120, h, "SCReAM Tx\nsets ECT(1) on RTP\nreacts to CE (l4sAlpha)", ACCENT, fs=7)
    box(g, 178, yb, 116, h, "Bottleneck AQM\nCE-marks instead\nof dropping", AMBER, fs=7)
    box(g, 344, yb, 118, h, "SCReAM Rx\nreads ECN-CE\n(IP_RECVTOS)", ACCENT2, fs=7)
    arrow(g, 128, yb+h*0.6, 178, yb+h*0.6, col=INK, label="RTP ECT(1)")
    arrow(g, 294, yb+h*0.6, 344, yb+h*0.6, col=INK, label="CE-marked")
    arrow(g, 344+59, yb, 8+60, yb, col=ACCENT2, w=1.0, dash=True)
    g.add(String(235, yb-14, "RTCP feedback: 2-bit ECN per packet (RFC 8888)", fontName="Helvetica-Oblique", fontSize=7, fillColor=ACCENT2, textAnchor="middle"))
    return g

# ---------- page furniture ----------
def footer(canv, doc):
    canv.saveState()
    canv.setFont("Helvetica", 7.5); canv.setFillColor(colors.HexColor("#8a93a0"))
    canv.drawString(0.9*inch, 0.55*inch, "ECN / L4S across SCREAMv2 and the 5G RAN")
    canv.drawRightString(7.6*inch, 0.55*inch, "p. %d" % doc.page)
    canv.setStrokeColor(MGRAY); canv.setLineWidth(0.4)
    canv.line(0.9*inch, 0.72*inch, 7.6*inch, 0.72*inch)
    canv.restoreState()

story = []

# ===== Title block =====
story.append(P("ECN and L4S across the SCREAMv2 + 5G RAN stack", title_s))
story.append(P("What is supported in the code, what the standards say, and the uplink question", sub_s))
story.append(Spacer(1,3))
story.append(P("A code- and standards-grounded research note for the GstreamerExp testbed. "
               "Every code claim links to the exact file and line on GitHub; "
               "standards claims cite an RFC / 3GPP TS / URL. Claims read from code are <b>measured</b>; "
               "design intent is marked <b>inferred</b>.", meta_s))
story.append(Spacer(1,8))

story.append(callout("Bottom line (the three questions)",
    "<b>1. GStreamer SCREAMv2 supports ECN</b> fully in the C++ library (ECT marking, CE read, RFC&nbsp;8888 feedback, "
    "and both a classic and an L4S/scalable response). The Rust <i>gstscream</i> plugin wires ECN on the <i>receiver</i> "
    "only; sender ECT-marking is a TODO &mdash; ECN on the wire is set instead by the <a href='https://github.com/EricssonResearch/scream/blob/05284fd/gstscream/udp_ecn_diff.txt' color='#1f4e79'><font name='Courier' size=8>udp_ecn_diff.txt</font></a> patch to GStreamer's <a href='https://gstreamer.freedesktop.org/documentation/udp/multiudpsink.html' color='#1f4e79'><font name='Courier' size=8>multiudpsink</font></a>.<br/>"
    "<b>2. srsRAN does not mark ECN.</b> The only ECN in the tree is an unimplemented 3GPP Rel-18 E1AP signalling stub. "
    "The user plane is pass-through, so it <i>preserves</i> a pre-set ECN field but never <i>sets</i> one. Mainline OAI is the same.<br/>"
    "<b>3. Uplink ECN marking is standardized and already demonstrated &mdash; it is just missing from mainline open stacks.</b> "
    "3GPP Rel-18 marks at the <b>gNB</b> (and UPF) for <b>both directions</b>, and <b>L4Span</b> (Princeton, CoNEXT) implements "
    "it on <b>srsRAN</b> for uplink and downlink. So the first-hop uplink case is achievable today; the gap is implementing it in "
    "mainline srsRAN / OAI, not the standard.", bg=colors.HexColor("#eaf1f7"), bar=ACCENT))

# ===== 1. Fundamentals =====
story.append(P("1&nbsp;&nbsp;Fundamentals", h1_s))
story.append(P("<b>ECN</b> (Explicit Congestion Notification) lets a router signal congestion by <i>marking</i> a packet "
    "instead of dropping it. The signal lives in the low 2 bits of the IP header's traffic-class byte:", body_s))
story.append(tbl([
    ["Codepoint","Bits","Meaning"],
    ["Not-ECT","00","Sender is not ECN-capable; routers can only drop."],
    ["ECT(0)","10","ECN-capable, classic."],
    ["ECT(1)","01","ECN-capable, the L4S identifier."],
    ["CE","11","Congestion Experienced: a router marked this packet."],
], [1.1*inch,0.7*inch,4.9*inch]))
story.append(Spacer(1,6))
story.append(P("<b>L4S</b> (Low Latency, Low Loss, Scalable throughput) builds on ECN. Senders mark their packets "
    "<b>ECT(1)</b>; an L4S-aware bottleneck puts them in a separate low-latency queue and <b>marks CE early and often, "
    "instead of dropping</b>, as soon as queue delay starts to rise. A <i>scalable</i> sender (DCTCP-style) reacts in "
    "proportion to how often it is marked, so it sits at a shallow, stable queue.", body_s))
story.append(callout("Correcting one common picture",
    "L4S is <b>not</b> &ldquo;two queues where marked traffic is sent before regular traffic.&rdquo; The L4S queue gets "
    "<i>conditional</i> priority for low latency, but a <b>Coupled AQM</b> ties its marking rate to the classic queue's drop "
    "rate so the two stay fair over time (RFC&nbsp;9332). The latency win comes from the sender reacting to <i>early, frequent "
    "CE marks</i>, not from marked packets jumping the line.", bg=colors.HexColor("#fdf6e9"), bar=AMBER))
story.append(Spacer(1,2))
story.append(KeepTogether([diag_dualq()]))
story.append(Spacer(1,2))
story.append(P("<b>SCReAM</b> (RFC&nbsp;8298) is Ericsson's self-clocked congestion control for real-time RTP media. It adapts "
    "the media bitrate to delay, loss, and ECN. With L4S it uses the scalable response. The receiver echoes the 2-bit ECN of "
    "every packet back to the sender in an <b>RFC&nbsp;8888</b> RTCP feedback report, which is how a CE mark reaches the encoder.", body_s))
story.append(KeepTogether([diag_loop()]))

# ===== 2. Problem context =====
story.append(P("2&nbsp;&nbsp;Problem context", h1_s))
story.append(P("Interactive media (cloud gaming, VR/XR, conversational video) needs low latency <i>under load</i>. When a link "
    "fills, classic congestion control only learns about it from loss or growing delay, both of which arrive late and hurt "
    "responsiveness. ECN/L4S gives the sender an <i>early</i> congestion signal, so it can trim its rate before the queue grows.", body_s))
story.append(P("The cellular link is usually the bottleneck, and it is <b>asymmetric</b>:", body_s))
story.append(B("<b>Downlink</b> (server &rarr; UE) is the conventional case. Cloud-gaming frames flow down; the bottleneck and "
    "the marking both sit on the network side. This is what most L4S work and the T-Mobile deployment target."))
story.append(B("<b>Uplink</b> (UE &rarr; server) is your case: the bottleneck is the <b>first hop</b>, the UE's own radio "
    "transmission. The packets that need marking are leaving the UE, so the marking has to happen at the UE or at the gNB's "
    "uplink buffer. This is the harder, less-served direction."))
story.append(KeepTogether([diag_5gpath()]))

# ===== 3. Findings =====
story.append(PageBreak())
story.append(P("3&nbsp;&nbsp;Findings: does the stack support ECN?", h1_s))

story.append(P("3.1&nbsp;&nbsp;GStreamer SCREAMv2 &mdash; yes, with a sender-plugin gap", h2_s))
story.append(P("The SCReAM C++ library (<font name='Courier' size=8>github.com/EricssonResearch/scream</font>) does the "
    "full ECN loop (measured):", body_s))
story.append(B("<b>Sender sets ECT</b> on the socket with <font name='Courier' size=8>IP_TOS</font> / "
    "<font name='Courier' size=8>IPV6_TCLASS</font> &mdash; <a href='https://github.com/EricssonResearch/scream/blob/05284fd/code/scream_sender.cpp#L552' color='#1f4e79'><font name='Courier' size=8>scream_sender.cpp:552</font></a>."))
story.append(B("<b>Receiver reads CE</b> via <font name='Courier' size=8>IP_RECVTOS</font> + "
    "<font name='Courier' size=8>recvmsg</font> cmsg &mdash; <a href='https://github.com/EricssonResearch/scream/blob/05284fd/code/scream_receiver.cpp#L288' color='#1f4e79'><font name='Courier' size=8>scream_receiver.cpp:288</font></a>."))
story.append(B("<b>Feedback</b> carries the 2-bit ECN per RFC&nbsp;8888 &mdash; <a href='https://github.com/EricssonResearch/scream/blob/05284fd/code/ScreamRx.cpp#L198' color='#1f4e79'><font name='Courier' size=8>ScreamRx.cpp:198</font></a>."))
story.append(B("<b>Controller reacts two ways</b> &mdash; classic (cut the window by <font name='Courier' size=8>kEcnCeBeta=0.9</font>, "
    "<a href='https://github.com/EricssonResearch/scream/blob/05284fd/code/ScreamV2Tx.h#L33' color='#1f4e79'><font name='Courier' size=8>ScreamV2Tx.h:33</font></a>) and <b>scalable / L4S</b> (a DCTCP-like <font name='Courier' size=8>l4sAlpha</font> "
    "from the fraction of marked bytes, <a href='https://github.com/EricssonResearch/scream/blob/05284fd/code/ScreamV2Tx.cpp#L737' color='#1f4e79'><font name='Courier' size=8>ScreamV2Tx.cpp:737</font></a>). It can even synthesise a "
    "&ldquo;virtual&rdquo; CE from queue delay when the network does not mark."))
story.append(P("The Rust <i>gstscream</i> GStreamer plugin only wires part of this:", body_s))
story.append(tbl([
    ["Stage","C++ library","gstscream plugin"],
    ["Sender sets ECT(1)","Yes (socket option)","<b>No</b> &mdash; TODO at <a href='https://github.com/EricssonResearch/scream/blob/05284fd/gstscream/src/sender.rs#L71' color='#1f4e79'><font name='Courier' size=7>sender.rs:71</font></a>; set via the <a href='https://github.com/EricssonResearch/scream/blob/05284fd/gstscream/udp_ecn_diff.txt' color='#1f4e79'><font name='Courier' size=7>udp_ecn_diff.txt</font></a> patch to <a href='https://gstreamer.freedesktop.org/documentation/udp/multiudpsink.html' color='#1f4e79'><font name='Courier' size=7>multiudpsink</font></a>"],
    ["Receiver reads CE","Yes","Yes, behind the <font name='Courier' size=7>ecn-enabled</font> feature (<a href='https://github.com/EricssonResearch/scream/blob/05284fd/gstscream/src/screamrx/ecn.rs#L27' color='#1f4e79'><font name='Courier' size=7>screamrx/ecn.rs</font></a>)"],
    ["RFC 8888 feedback","Yes","Yes"],
    ["L4S / classic response","Yes","Yes (in the wrapped library)"],
], [1.55*inch,1.15*inch,4.0*inch]))
story.append(Spacer(1,5))
story.append(callout("The GStreamer ECN patch (!2717)",
    "Sender-side ECN in GStreamer comes from an upstream merge request, "
    "<a href='https://gitlab.freedesktop.org/gstreamer/gstreamer/-/merge_requests/2717' color='#1f4e79'>GStreamer&nbsp;!2717</a> "
    "(&ldquo;udp: Add support for ECN to udpsink and udpsrc&rdquo;, BBC&nbsp;R&amp;D, 2022). It adds a "
    "<font name='Courier' size=8>set-ecn</font> property to the UDP elements and a new core "
    "<font name='Courier' size=8>GstNetEcnMeta</font> buffer metadata, so a pipeline can stamp ECT(1) on outgoing "
    "packets and read ECN-CE on incoming ones. The merge request is still open (last updated 2023), so the feature "
    "is not yet in released GStreamer; gstscream ships it as "
    "<a href='https://github.com/EricssonResearch/scream/blob/05284fd/gstscream/udp_ecn_diff.txt' color='#1f4e79'><font name='Courier' size=8>udp_ecn_diff.txt</font></a>, "
    "a 2024 re-base of !2717 applied to a from-source GStreamer build.", bg=LGRAY, bar=ACCENT2))
story.append(Spacer(1,5))
story.append(callout("To actually run SCREAM-L4S with ECN in GStreamer",
    "Build a patched GStreamer (<a href='https://github.com/EricssonResearch/scream/blob/05284fd/gstscream/udp_ecn_diff.txt' color='#1f4e79'><font name='Courier' size=8>udp_ecn_diff.txt</font></a> adds a "
    "<font name='Courier' size=8>set-ecn</font> property to <a href='https://gstreamer.freedesktop.org/documentation/udp/multiudpsink.html' color='#1f4e79'><font name='Courier' size=8>multiudpsink</font></a>), build gstscream with "
    "<font name='Courier' size=8>ECN_ENABLED=1</font>, select ECT(1) for L4S mode on the sender, and confirm the receiver prints "
    "<font name='Courier' size=8>ecn-enabled</font>. Steps are in <a href='https://github.com/EricssonResearch/scream/blob/05284fd/gstscream/README.md' color='#1f4e79'><font name='Courier' size=8>README.md</font></a>.",
    bg=LGRAY, bar=ACCENT2))

story.append(P("3.2&nbsp;&nbsp;srsRAN / OAI &mdash; no active marking", h2_s))
story.append(P("In srsRAN <font name='Courier' size=8>release_25_10</font>, the string <font name='Courier' size=8>ecn</font> appears "
    "<b>only</b> in the E1AP ASN.1 stub (<a href='https://github.com/srsran/srsRAN_Project/blob/d2f4b70/lib/asn1/e1ap/e1ap_ies.cpp' color='#1f4e79'><font name='Courier' size=8>e1ap_ies.cpp</font></a>): the 3GPP Rel-18 "
    "&ldquo;ECN marking / congestion report&rdquo; information element is <i>defined but not implemented</i>. There is no "
    "queue-occupancy-based CE-marking anywhere in SDAP, PDCP, RLC, MAC, or the scheduler (measured: a search for active marking "
    "returns nothing). The only socket-level marking is <i>control-plane</i> DSCP "
    "(<a href='https://github.com/srsran/srsRAN_Project/blob/d2f4b70/lib/gateways/udp_network_gateway_impl.cpp#L480' color='#1f4e79'><font name='Courier' size=8>udp_network_gateway_impl.cpp:480</font></a>).", body_s))
story.append(P("The user plane only forwards. The GTP-U uplink path adds the GTP-U header and a QFI extension and never touches "
    "the inner IP packet (<a href='https://github.com/srsran/srsRAN_Project/blob/d2f4b70/lib/gtpu/gtpu_tunnel_ngu_tx_impl.h#L56' color='#1f4e79'><font name='Courier' size=8>gtpu_tunnel_ngu_tx_impl.h:56</font></a>), so a pre-set ECN field is "
    "<b>preserved end to end</b> (measured) but srsRAN never <i>sets</i> one. Mainline OpenAirInterface is the same: no "
    "user-plane L4S marking. The srsRAN Project ships only the gNB (no NR UE), so the UE uplink path cannot be read from its source.", body_s))

story.append(P("3.3&nbsp;&nbsp;The research bridge: L4Span on srsRAN", h2_s))
story.append(callout("L4Span (Princeton PAWS, ACM CoNEXT) &mdash; the answer to the uplink question",
    "<b>L4Span</b> implements L4S congestion signalling on <b>srsRAN</b> (~2,000 lines of C++). It predicts the RAN's queue "
    "occupancy at millisecond timescales and performs ECN-CE marking for both low-latency and classic flows, with explicit "
    "<b>uplink and downlink</b> marking (code under <a href='https://github.com/PrincetonUniversity/L4Span/tree/cef83f8/lib/mark' color='#1f4e79'><font name='Courier' size=8>lib/mark</font></a>). It stays 3GPP / O-RAN "
    "compliant and reports up to ~98% lower one-way delay. This is a working precedent for exactly your setup: SCReAM-L4S over "
    "srsRAN with uplink marking.<br/>"
    "<font size=8>arXiv 2510.02682 &middot; github.com/PrincetonUniversity/L4Span &middot; CoNEXT</font>",
    bg=colors.HexColor("#e9f3ec"), bar=GREEN))
story.append(Spacer(1,5))
story.append(tbl([
    ["","Set ECT (sender)","Read / react to CE","CE-mark in network","Uplink marking"],
    ["SCREAMv2 (C++ lib)","Yes","Yes","n/a (endpoint)","n/a"],
    ["gstscream plugin","Via patched udpsink","Yes (receiver)","n/a","n/a"],
    ["srsRAN mainline","No","No","<b>No</b> (E1AP stub only)","No (preserves field)"],
    ["OAI mainline","No","No","No","No"],
    ["L4Span (research, on srsRAN)","n/a","n/a","<b>Yes</b>","<b>Yes</b>"],
], [1.7*inch,1.0*inch,1.05*inch,1.05*inch,0.9*inch], font=8))

# ===== 4. Uplink + standards =====
story.append(P("4&nbsp;&nbsp;The uplink question and the standards", h1_s))
story.append(B("<b>3GPP Rel-18</b> (TS&nbsp;23.501, L4S support, clause&nbsp;5.37) standardises ECN marking for L4S at the "
    "<b>NG-RAN (gNB)</b> and/or the <b>UPF</b>, selectable by the operator. The gNB marks based on radio congestion, and the "
    "feature covers <b>both uplink and downlink</b>. So uplink first-hop marking is specified, not a gap in the standard. "
    "<i>(sourced via secondary summaries of the TS; pin the exact clause text before quoting.)</i>"))
story.append(B("<b>Open implementations lag the standard.</b> srsRAN and OAI mainline do not implement it; L4Span does (research)."))
story.append(B("<b>T-Mobile</b> announced production L4S on its 5G-Advanced network (July&nbsp;2025), the first US carrier to do "
    "so at scale, for apps like NVIDIA GeForce&nbsp;NOW, FaceTime, XR, and remote driving. L4S is ECN-based; the public "
    "materials emphasise the apps and do not break out uplink vs downlink. <font size=8>t-mobile.com/news/network/unlock-l4s-5g-advanced</font>"))
story.append(callout("Net answer to &ldquo;do UE stacks support uplink ECN?&rdquo;",
    "Standardised: <b>yes</b> (Rel-18, gNB/UPF, UL+DL). Mainline open RAN (srsRAN/OAI): <b>no</b>. Demonstrated on srsRAN in "
    "research: <b>yes</b> (L4Span). Your premise &mdash; congestion at the uplink first hop &mdash; is exactly what gNB-side "
    "Rel-18 marking targets; the work is porting/enabling it, not inventing it.", bg=colors.HexColor("#eaf1f7"), bar=ACCENT))

# ===== 5. What to do =====
story.append(P("5&nbsp;&nbsp;Practical options for the testbed", h1_s))
story.append(B("<b>Media side:</b> run SCREAM in <b>L4S mode</b> (ECT(1)) with the patched GStreamer; the sender already reacts "
    "to CE with the scalable <font name='Courier' size=8>l4sAlpha</font> response."))
story.append(B("<b>RAN marking, three paths:</b> (a) port/use <b>L4Span</b> on srsRAN for real UL+DL CE-marking (closest to your "
    "goal); (b) for a controlled experiment, put an <b>L4S AQM</b> (<a href='https://www.rfc-editor.org/rfc/rfc9332' color='#1f4e79'>DualPI2</a> / fq_codel with L4S) on a Linux bottleneck in the "
    "path and let srsRAN pass the ECN field through (which it does); (c) rely on SCReAM's <b>virtual-CE</b> (queue-delay) mode "
    "where no network marking exists &mdash; useful as a fallback but not a true L4S signal."))
story.append(B("<b>Verify pass-through</b> first: confirm a CE set upstream survives the srsRAN bearer (the GTP-U path does not "
    "touch the inner IP header, so it should)."))

# ===== references =====
story.append(P("References", h1_s))
refstyle = S("ref", parent=body_s, fontSize=8.4, leading=12, spaceAfter=2, alignment=TA_LEFT)
def link(u, text=None):
    return '<a href="%s" color="#1f4e79">%s</a>' % (u, text or u.replace("https://",""))
def reflist(items):
    for r in items:
        story.append(Paragraph("&bull;&nbsp; "+r, refstyle))

story.append(P("Public code repositories (open access)", h2_s))
reflist([
 "<b>SCReAM / SCREAMv2</b> + the <i>gstscream</i> GStreamer plugin &mdash; " + link("https://github.com/EricssonResearch/scream"),
 "<b>srsRAN Project</b> (open-source 5G gNB / CU / DU) &mdash; " + link("https://github.com/srsran/srsRAN_Project"),
 "<b>OpenAirInterface 5G</b> (OAI) &mdash; " + link("https://gitlab.eurecom.fr/oai/openairinterface5g"),
 "<b>L4Span</b> (L4S ECN marking on srsRAN, research prototype) &mdash; " + link("https://github.com/PrincetonUniversity/L4Span"),
])
story.append(P("Standards and sources", h2_s))
reflist([
 "RFC 8298 &mdash; SCReAM (self-clocked rate adaptation for multimedia). " + link("https://www.rfc-editor.org/rfc/rfc8298"),
 "RFC 8888 &mdash; RTCP congestion-control feedback (echoes the 2-bit ECN). " + link("https://www.rfc-editor.org/rfc/rfc8888"),
 "RFC 9330 / 9331 / 9332 &mdash; L4S architecture, ECN protocol (ECT(1)), DualQ Coupled AQM.",
 "RFC 3168 &mdash; ECN codepoints in the IP header.",
 "3GPP TS 23.501 (Rel-18), clause 5.37 &mdash; L4S support; ECN marking at NG-RAN and/or UPF, UL+DL.",
 "L4Span (paper) &mdash; Princeton PAWS, ACM CoNEXT. " + link("https://arxiv.org/abs/2510.02682"),
 "T-Mobile &mdash; L4S on 5G-Advanced (Jul 2025). " + link("https://www.t-mobile.com/news/network/unlock-l4s-5g-advanced"),
])
story.append(P("Code files cited (click to open the exact file on GitHub)", h2_s))
reflist([
 'SCReAM (master): '
 '<a href="https://github.com/EricssonResearch/scream/blob/05284fd/code/scream_sender.cpp#L552" color="#1f4e79">scream_sender.cpp</a>, '
 '<a href="https://github.com/EricssonResearch/scream/blob/05284fd/code/scream_receiver.cpp#L288" color="#1f4e79">scream_receiver.cpp</a>, '
 '<a href="https://github.com/EricssonResearch/scream/blob/05284fd/code/ScreamRx.cpp#L198" color="#1f4e79">ScreamRx.cpp</a>, '
 '<a href="https://github.com/EricssonResearch/scream/blob/05284fd/code/ScreamV2Tx.cpp#L737" color="#1f4e79">ScreamV2Tx.cpp</a>, '
 '<a href="https://github.com/EricssonResearch/scream/blob/05284fd/gstscream/src/sender.rs#L71" color="#1f4e79">gstscream/sender.rs</a>, '
 '<a href="https://github.com/EricssonResearch/scream/blob/05284fd/gstscream/src/screamrx/ecn.rs#L27" color="#1f4e79">gstscream/screamrx/ecn.rs</a>',
 'srsRAN @ release_25_10 (d2f4b70): '
 '<a href="https://github.com/srsran/srsRAN_Project/blob/d2f4b70/lib/asn1/e1ap/e1ap_ies.cpp" color="#1f4e79">e1ap_ies.cpp</a>, '
 '<a href="https://github.com/srsran/srsRAN_Project/blob/d2f4b70/lib/gtpu/gtpu_tunnel_ngu_tx_impl.h#L56" color="#1f4e79">gtpu_tunnel_ngu_tx_impl.h:56</a>, '
 '<a href="https://github.com/srsran/srsRAN_Project/blob/d2f4b70/lib/gateways/udp_network_gateway_impl.cpp#L480" color="#1f4e79">udp_network_gateway_impl.cpp:480</a>',
])

doc = SimpleDocTemplate(OUT, pagesize=letter,
    leftMargin=0.9*inch, rightMargin=0.9*inch, topMargin=0.8*inch, bottomMargin=0.9*inch,
    title="ECN / L4S across SCREAMv2 and the 5G RAN", author="Research note")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("WROTE", OUT, os.path.getsize(OUT), "bytes")
