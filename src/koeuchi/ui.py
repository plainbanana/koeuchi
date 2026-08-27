"""Floating recording pill (NSPanel).

It must never steal focus from the frontmost app, or Cmd+V would land in the
wrong window: NSWindowStyleMaskNonactivatingPanel + accessory activation
policy + orderFrontRegardless keep it frontmost without activating.

AppKit must be driven from the main thread, so externally callable methods
hop there via AppHelper.callAfter.
"""

from __future__ import annotations

import time
from typing import Callable

import AppKit
import objc
from PyObjCTools import AppHelper

PANEL_W, PANEL_H = 352.0, 60.0
_BOTTOM_MARGIN = 90.0
_MSG_MAX_W = 640.0
_MSG_MAX_SCREEN_RATIO = 0.4
_MSG_FONT_SIZE = 13.0
_MSG_PAD = 18.0
_MSG_MIN_W = 150.0
_BARS = 30
_BAR_W, _BAR_GAP = 4.0, 4.0
_BAR_MAX_H = 42.0
_BTN_D, _BTN_CX = 22.0, 21.0
_FPS = 30.0


def schedule_timer(interval: float, repeats: bool, block) -> AppKit.NSTimer:
    """Register a timer that keeps firing during menu tracking etc."""
    timer = AppKit.NSTimer.timerWithTimeInterval_repeats_block_(interval, repeats, block)
    AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(timer, AppKit.NSRunLoopCommonModes)
    return timer


def _format_elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s}s" if s < 60 else f"{s // 60}:{s % 60:02d}"


def _attributed_message(message: str):
    style = AppKit.NSMutableParagraphStyle.alloc().init()
    style.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(
        message,
        {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(_MSG_FONT_SIZE),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
            AppKit.NSParagraphStyleAttributeName: style,
        },
    )


def _measure_message(message: str, max_width: float):
    """Return the (width, height) of the wrapped text."""
    bound = _attributed_message(message).boundingRectWithSize_options_(
        AppKit.NSMakeSize(max_width, 100000.0),
        AppKit.NSStringDrawingUsesLineFragmentOrigin,
    )
    return bound.size.width, bound.size.height


class KoeuchiPillView(AppKit.NSView):
    """Pill body: draws either the level meter or a status message."""

    def initWithFrame_(self, frame):
        self = objc.super(KoeuchiPillView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.bars = [0.0] * _BARS
        self.message = None  # None -> meter, str -> message
        self.elapsed_text = ""
        self.on_stop = None
        return self

    def _button_rect(self):
        return AppKit.NSMakeRect(
            _BTN_CX - _BTN_D / 2, (self.bounds().size.height - _BTN_D) / 2, _BTN_D, _BTN_D
        )

    def drawRect_(self, rect):
        bounds = self.bounds()
        radius = min(PANEL_H, bounds.size.height) / 2
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.11, 0.93).setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, radius, radius
        ).fill()

        if self.message is not None:
            self._draw_message(_MSG_PAD, bounds)
            return

        btn = self._button_rect()
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.91, 0.30, 0.30, 1.0).setFill()
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(btn).fill()
        AppKit.NSColor.whiteColor().setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSInsetRect(btn, 7.0, 7.0), 1.5, 1.5
        ).fill()

        x0 = _BTN_CX + _BTN_D / 2 + 10.0
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.40, 0.80, 1.0, 1.0).setFill()
        cy = bounds.size.height / 2
        for i, level in enumerate(self.bars):
            h = max(3.0, level * _BAR_MAX_H)
            bar = AppKit.NSMakeRect(x0 + i * (_BAR_W + _BAR_GAP), cy - h / 2, _BAR_W, h)
            AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar, _BAR_W / 2, _BAR_W / 2
            ).fill()

        if self.elapsed_text:
            text = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                self.elapsed_text,
                {
                    AppKit.NSFontAttributeName: AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
                        12.0, AppKit.NSFontWeightRegular
                    ),
                    AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedWhite_alpha_(
                        1.0, 0.75
                    ),
                },
            )
            size = text.size()
            text.drawAtPoint_(
                AppKit.NSMakePoint(
                    bounds.size.width - size.width - 16.0, cy - size.height / 2
                )
            )

    def _draw_message(self, x0: float, bounds) -> None:
        width = bounds.size.width - x0 - _MSG_PAD
        text = _attributed_message(self.message)
        height = text.boundingRectWithSize_options_(
            AppKit.NSMakeSize(width, 100000.0),
            AppKit.NSStringDrawingUsesLineFragmentOrigin,
        ).size.height
        height = min(height, bounds.size.height - 12.0)
        text.drawInRect_(
            AppKit.NSMakeRect(x0, (bounds.size.height - height) / 2, width, height)
        )

    def mouseDown_(self, event):
        if self.message is not None:
            return
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        if self.on_stop is not None and AppKit.NSPointInRect(point, self._button_rect()):
            self.on_stop()


class Overlay:
    """Recording pill controller. show/hide may be called from any thread."""

    def __init__(self, levels: Callable[[], list[float]], on_stop: Callable[[], None]):
        self._levels = levels
        self._on_stop = on_stop
        self._panel = None
        self._view = None
        self._timer = None
        self._hide_timer = None
        self._rec_started = 0.0

    def build(self) -> None:
        """Create the panel; call on the main thread before the event loop."""
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, PANEL_W, PANEL_H),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setHasShadow_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        view = KoeuchiPillView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, PANEL_W, PANEL_H)
        )
        view.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        view.on_stop = self._on_stop
        panel.setContentView_(view)
        self._panel, self._view = panel, view
        self._set_panel_size(PANEL_W, PANEL_H)

    def _set_panel_size(self, width: float, height: float) -> None:
        """Resize the panel, keeping it bottom-centered on screen."""
        screen = AppKit.NSScreen.mainScreen().visibleFrame()
        self._panel.setFrame_display_(
            AppKit.NSMakeRect(
                screen.origin.x + (screen.size.width - width) / 2,
                screen.origin.y + _BOTTOM_MARGIN,
                width,
                height,
            ),
            True,
        )

    # --- callable from any thread ---

    def show_recording(self) -> None:
        AppHelper.callAfter(self._show_recording)

    def show_message(self, message: str) -> None:
        AppHelper.callAfter(self._show_message, message)

    def flash_message(self, message: str, duration: float) -> None:
        AppHelper.callAfter(self._flash_message, message, duration)

    def hide(self) -> None:
        AppHelper.callAfter(self._hide)

    # --- main thread only ---

    def _show_recording(self) -> None:
        if self._panel is None:
            return
        self._cancel_hide_timer()
        self._view.message = None
        self._view.bars = [0.0] * _BARS
        self._rec_started = time.monotonic()
        self._view.elapsed_text = "0s"
        self._set_panel_size(PANEL_W, PANEL_H)
        self._panel.orderFrontRegardless()
        if self._timer is None:
            self._timer = schedule_timer(1.0 / _FPS, True, lambda timer: self._animate())

    def _show_message(self, message: str) -> None:
        if self._panel is None:
            return
        self._stop_timer()
        self._cancel_hide_timer()
        self._view.message = message
        text_w, text_h = _measure_message(message, _MSG_MAX_W)
        width = max(_MSG_MIN_W, min(_MSG_MAX_W, text_w + 2.0) + 2 * _MSG_PAD)
        max_h = AppKit.NSScreen.mainScreen().visibleFrame().size.height * _MSG_MAX_SCREEN_RATIO
        height = max(PANEL_H, min(text_h + 24.0, max_h))
        self._set_panel_size(width, height)
        self._view.setNeedsDisplay_(True)
        self._panel.orderFrontRegardless()

    def _flash_message(self, message: str, duration: float) -> None:
        if self._panel is None:
            return
        self._show_message(message)
        self._hide_timer = schedule_timer(duration, False, lambda timer: self._hide())

    def _hide(self) -> None:
        if self._panel is None:
            return
        self._stop_timer()
        self._cancel_hide_timer()
        self._panel.orderOut_(None)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def _cancel_hide_timer(self) -> None:
        if self._hide_timer is not None:
            self._hide_timer.invalidate()
            self._hide_timer = None

    def _animate(self) -> None:
        # Drawing runs at 33ms vs 16ms blocks, so take the peak of the tail.
        levels = self._levels()
        bars = self._view.bars
        bars[:] = bars[1:] + [max(levels[-2:]) if levels else 0.0]
        self._view.elapsed_text = _format_elapsed(time.monotonic() - self._rec_started)
        self._view.setNeedsDisplay_(True)


class NullOverlay:
    """No-op implementation used when overlay=false."""

    def build(self) -> None: ...
    def show_recording(self) -> None: ...
    def show_message(self, message: str) -> None: ...
    def flash_message(self, message: str, duration: float) -> None: ...
    def hide(self) -> None: ...
