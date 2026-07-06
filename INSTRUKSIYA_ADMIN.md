# 🔐 Admin uchun yo'riqnoma — YouTube AI Mentor bot

Bu hujjat botni boshqaradigan adminlar (asosiy va yordamchi) uchun.

---

## 1. Bot nima qiladi

Bot — onlayn kursning AI yordamchisi. Faqat **kurs talabalari** foydalanadi. Ikki qism:

1. **O'quvchilar uchun** — kurs bo'yicha savol-javob, YouTube SEO, rasm (avatar/banner/thumbnail) yaratish.
2. **Adminlar uchun** — talabalarni qo'shish/o'chirish, muddat boshqaruvi, statistika.

---

## 2. Admin turlari

| | Asosiy admin | Yordamchi admin |
|---|---|---|
| O'quvchi qo'shish/o'chirish | ✅ | ✅ |
| Statistika, ro'yxatlar | ✅ | ✅ |
| Ban / kick | ✅ | ✅ |
| **Admin qo'shish/o'chirish** | ✅ | ❌ |

- **Asosiy admin** — Railway'dagi `ADMIN_USER_IDS` ro'yxatidagilar. Faqat ular yordamchi admin qo'sha/o'chira oladi.
- **Yordamchi admin** — bot orqali qo'shiladi (`/add_admin`), bazada saqlanadi.

### Yordamchi admin qo'shish
1. Yordamchi botga `/start`, keyin `/myid` yozadi → o'z ID'sini ko'radi.
2. Siz: `/add_admin 987654321`
3. O'chirish: `/remove_admin 987654321`

---

## 3. O'quvchini qanday qo'shasiz

Bot **telefon raqami** orqali tanidi. Siz raqamni ro'yxatga qo'shasiz → o'quvchi botga kirib o'sha raqamni yozadi → tasdiqlanadi.

### Bitta raqam
```
/add_phone +998901234567          → 4 oy (default)
/add_phone +998901234567 6        → 6 oy
/add_phone +998901234567 0        → cheksiz
```

### Ko'p raqam (fayl bilan)
`.txt` yoki `.csv` faylga raqamlarni yozing (har qatorga bitta), faylni botga yuboring va **izoh (caption)** ga:
```
/upload_phones        → 4 oy
/upload_phones 6      → 6 oy
/upload_phones 0      → cheksiz
```

### Formatlar
Raqam quyidagicha bo'lishi mumkin — bot avtomat to'g'rilaydi:
`+998901234567`, `998901234567`, `901234567`, `90 123 45 67`, `90-123-45-67`

---

## 4. Ikki ro'yxat — farqini yodda tuting

| Buyruq | Nimani ko'rsatadi |
|---|---|
| `/list_phones` | Siz **qo'shgan** raqamlar (ruxsat ro'yxati). ✅ = ro'yxatdan o'tgan, ⏳ = hali kirmagan |
| `/list_users` | Botga **kirib, raqamini tasdiqlagan** talabalar |

Raqam qo'shsangiz → darhol `/list_phones` da ko'rinadi. `/list_users` da esa faqat o'sha odam botga kirib raqamini yozgandan keyin chiqadi.

---

## 5. Muddat va avtomat chiqarish

- Har o'quvchiga muddat beriladi (default **4 oy**, `/add_phone` da o'zgartirsa bo'ladi).
- Muddat tugagach bot uni **avtomat** kurs guruh/kanalidan chiqaradi (har soatda tekshiradi).
- Buning ishlashi uchun `KICK_CHAT_IDS` sozlanishi kerak (6-bo'limga qarang).
- `/list_expiring` — yaqin 7 kun ichida muddati tugaydiganlar.
- `/kick_now` — muddati o'tganlarni darhol chiqarish (odatda avtomat bo'ladi).

---

## 6. Bir martalik sozlash (avtomat chiqarish uchun)

1. Botni kurs **guruhi** va **kanaliga** admin qiling ("Ban users" ruxsati bilan).
2. O'sha guruh/kanalda `/chat_id` yozing → ID'ni oladi (masalan `-1001234567890`).
3. Railway → Variables → `KICK_CHAT_IDS` ga qo'shing (vergul bilan bir nechta):
   ```
   KICK_CHAT_IDS=-1001234567890,-1009876543210
   ```

---

## 7. Akkount almashtirish holatlari

- **Bitta raqam = bitta Telegram akkaunt.** Boshqa akkount band raqamni yozsa: "⛔ allaqachon boshqa akkaunt bilan ro'yxatdan o'tgan".
- O'quvchi Telegram akkauntini almashtirsa: `/free_phone +998901234567` → raqam bo'shaydi, yangi akkaunt qayta kira oladi (raqam ruxsat ro'yxatida qoladi).
- `/remove_phone +998901234567` → raqamni **butunlay** o'chiradi (umuman kira olmaydi).

---

## 8. Barcha admin buyruqlari

**Ko'rish:**
- `/admin_help` — buyruqlar ro'yxati
- `/admin_stats` — umumiy statistika
- `/list_users` — ro'yxatdan o'tgan talabalar
- `/list_phones` — ruxsat ro'yxatidagi raqamlar
- `/list_expiring` — muddati tugayotganlar (7 kun)

**Telefon ro'yxati:**
- `/add_phone +998901234567 [oy]` — bitta raqam qo'shish
- `/remove_phone +998901234567` — raqamni o'chirish
- `/upload_phones` (fayl + caption) — ko'p raqam
- `/free_phone +998901234567` — raqamni bo'shatish (akkount almashtirish)

**Boshqaruv:**
- `/ban_user 123456789` — ban
- `/unban_user 123456789` — banni olib tashlash
- `/kick_now` — muddati o'tganlarni darhol chiqarish

**Adminlar (faqat asosiy admin):**
- `/add_admin 123456789` — yordamchi admin qo'shish
- `/remove_admin 123456789` — yordamchi adminni o'chirish
- `/list_admins` — adminlar ro'yxati

**Yordamchi:**
- `/chat_id` — joriy chat ID (KICK_CHAT_IDS uchun)
- `/myid` — o'z ID va admin holatingiz

---

## 9. Texnik ma'lumot

- **Hosting:** Railway, proyekt **alluring-dream** (servis "worker"). GitHub'ga (`oybekbozorovai/ai-kurator`) push qilinganda avtomat deploy bo'ladi.
- **MUHIM:** Hech qachon yangi Railway proyekti ochmang. Bitta bot tokeni bilan ikkita servis ishlasa — `Conflict` xatosi chiqadi va bot ishlamaydi.
- **Bilim bazasi:** kurs darslari transkriptlari index'da (GitHub'da saqlanadi). Yangi dars qo'shilsa, transkripsiya qilinib indexga yuklanadi.
- **Sozlamalar (Railway → Variables):** `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `ADMIN_USER_IDS`, `REPLICATE_API_TOKEN`, `DAILY_IMAGE_LIMIT`, `DAILY_TEXT_LIMIT`, `COURSE_ACCESS_MONTHS`, `KICK_CHAT_IDS`.
- **Ma'lumotlar:** o'quvchi telefonlari va adminlar Railway volume'dagi bazada (`data/auth.db`).

---

## 10. Tez-tez uchraydigan savollar

**Admin buyruqlari ishlamayapti?**
`/myid` yozing → "Admin: HA ✅" chiqishi kerak. Chiqmasa, `ADMIN_USER_IDS` da ID'ingiz borligini tekshiring.

**Bot javob bermayapti / goh ishlaydi goh yo'q?**
Ikkita bot bir vaqtda ishlayotgan bo'lishi mumkin (`Conflict`). Railway'da faqat bitta servis qolganiga ishonch hosil qiling.

**O'quvchi "raqamim topilmadi" deyapti?**
`/list_phones` da raqami bormi tekshiring. Yo'q bo'lsa `/add_phone` bilan qo'shing. Format farq qilsa ham bot to'g'rilaydi.
