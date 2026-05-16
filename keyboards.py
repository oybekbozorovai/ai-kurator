"""Inline tugmalar (klaviaturalar) — menyu va YouTube xizmatlari uchun."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Bosh menyu matni
MENU_TEXT = "🤖 Bosh menyu\n\nKerakli xizmatni tanlang 👇"

# Xizmat nomi -> emoji (tarix ro'yxati uchun)
SERVICE_EMOJI = {
    "channel_seo": "📺",
    "video_seo": "🎬",
    "avatar": "🖼",
    "banner": "🎨",
    "thumbnail": "🌅",
    "qa": "🎓",
}


def main_menu_kb() -> InlineKeyboardMarkup:
    """Asosiy menyu — barcha xizmatlar alohida tugma."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎓 Kurs bo'yicha savol", callback_data="menu:qa")],
        [InlineKeyboardButton(text="📺 Kanal SEO", callback_data="menu:channel_seo")],
        [InlineKeyboardButton(text="🎬 Video SEO", callback_data="menu:video_seo")],
        [InlineKeyboardButton(text="🖼 Avatar yaratish", callback_data="menu:avatar")],
        [InlineKeyboardButton(text="🎨 Banner yaratish", callback_data="menu:banner")],
        [InlineKeyboardButton(text="🌅 Thumbnail yaratish", callback_data="menu:thumbnail")],
        [InlineKeyboardButton(text="📂 Mening ishlarim", callback_data="menu:history")],
    ])


def home_kb() -> InlineKeyboardMarkup:
    """Faqat 'Bosh menyu' tugmasi."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="nav:home")],
    ])


def thumb_position_kb() -> InlineKeyboardMarkup:
    """Thumbnail matni joylashuvi."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬆️ Yuqorida", callback_data="thumb:pos:top"),
            InlineKeyboardButton(text="⬇️ Pastda", callback_data="thumb:pos:bottom"),
        ],
        [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="nav:home")],
    ])


def thumb_color_kb() -> InlineKeyboardMarkup:
    """Thumbnail matni rangi."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟡 Sariq", callback_data="thumb:color:yellow"),
            InlineKeyboardButton(text="⚪️ Oq", callback_data="thumb:color:white"),
        ],
        [
            InlineKeyboardButton(text="🔴 Qizil", callback_data="thumb:color:red"),
            InlineKeyboardButton(text="🟢 Yashil", callback_data="thumb:color:green"),
        ],
        [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="nav:home")],
    ])


def history_kb(items: list) -> InlineKeyboardMarkup:
    """Tarix ro'yxati — har bir ish alohida tugma.
    items: [(id, service, label, created_at), ...]
    """
    rows = []
    for item_id, service, label, _created in items:
        emoji = SERVICE_EMOJI.get(service, "📄")
        title = (label or service)[:35]
        rows.append([InlineKeyboardButton(
            text=f"{emoji} {title}", callback_data=f"hist:{item_id}"
        )])
    rows.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
