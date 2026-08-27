# Tài liệu tham số đầu vào — NSO AI-PC Fitting V2.1

> **Cập nhật 2026-08-24 theo V2.1 Design Baseline của thầy.** Ba thay đổi lớn ảnh
> hưởng tới cách nhập liệu:
>
> 1. **CSF giờ mang theo protocol** — thiết bị, tần số, thang đo. Dải Low/Mid/High
>    chỉ còn là *fallback*, không phải phép đo.
> 2. **VEP/ERG dạng số trần không còn làm tăng độ tin cậy.** Cần Z-score hoặc chỉ
>    số eye tracking có thang xác định. Số trần vẫn được lưu cho dữ liệu sau này.
> 3. **Bốn công thức cũ đã bỏ.** Tầng lâm sàng giờ xuất ra vector nhu cầu 0–1,
>    Design Engine mới ánh xạ sang hình học ba vùng A/B/C.

Mô tả **từng ô nhập liệu** trên giao diện: nó là gì, hệ thống dùng nó làm gì, và
đi tới đâu trong chuỗi xử lý.

Đi kèm là ghi chú về **mức độ tin cậy**: chỗ nào là công thức có cơ sở, chỗ nào là
hệ số mình tự đặt tạm, chỗ nào hiện chưa được dùng.

Chi tiết về từng hằng số bịa nằm ở [ASSUMPTIONS.md](ASSUMPTIONS.md); tài liệu này
nhìn từ phía **người nhập liệu** thay vì từ phía code.

---

## Cách đọc ký hiệu

| Ký hiệu | Nghĩa |
|---|---|
| 🟢 **Đầy đủ** | Ảnh hưởng tới thiết kế quang học thật (SA, vi cấu trúc, hình học) |
| 🔵 **Chỉ số** | Ảnh hưởng chỉ số / phenotype / dự báo, nhưng không đổi thiết kế |
| 🟣 **Chỉ hình học** | Chỉ đi vào file gia công, không qua chỉ số nào |
| 🟡 **Chỉ confidence** | Chỉ ảnh hưởng độ tin cậy, không đổi thiết kế |
| 🔴 **CHƯA DÙNG** | Nhập vào nhưng **hệ thống không dùng** |

Về hệ số:

| Ký hiệu | Nghĩa |
|---|---|
| ⚙️ | Hệ số **tự đặt tạm**, chưa hiệu chỉnh lâm sàng |
| 📐 | Công thức chuẩn / quan hệ định nghĩa được |

---

## Tổng quan

Ba tầng nhập liệu:

| Tầng | Bắt buộc | Vai trò |
|---|---|---|
| **Tier 1 — Quick Fitting** | Có | 13 ô, đủ để sinh thiết kế hoàn chỉnh |
| **Tier 2 — Advanced Clinical** | Không | Binocular, điều tiết, thần kinh thị giác đầy đủ |
| **Tier 3 — Research Mode** | Không | Protocol CSF, điện sinh lý, eye tracking, wavefront |

Chuỗi xử lý mà mọi input đều đi qua:

Bảy tầng, theo kiến trúc V2.1 của thầy:

```
Ô nhập
  └→ 1. Clinical phenotype      8 chỉ số + R/B/S/N/T      → hiện cho bác sĩ
       └→ 2. Functional requirement  vector nhu cầu 0–1   ← độc lập sản phẩm
            └→ 3. Optical target      mục tiêu D từng vùng
                 └→ 4. Design profile  9 candidate/mắt, joint OD/OS
                      └→ 5. Geometry projection   hai kênh riêng   [Design IP]
                           └→ 6. Manufacturing compensation  bù HC
                                └→ 7. Execution package    theo vendor  [Mfg IP]
```

Hai tầng đầu **không biết tròng kính tồn tại** — đó là ranh giới cho phép đổi sang
MR-8, PC hay kính áp tròng mà không viết lại tầng lâm sàng.

---

# TIER 1 — Quick Fitting

Tầng mặc định. Chỉ cần tầng này là bấm Generate ra kết quả đầy đủ.

## Section 1 — Bệnh nhân & khúc xạ

### Age (tuổi) 🟢

Tuổi bệnh nhân, năm.

**Dùng để:**
- `refractive_risk` — trẻ càng nhỏ nguy cơ tiến triển càng cao ⚙️ trọng số 0.20
- `neural_adaptation` — trẻ nhỏ thích nghi tốt hơn ⚙️ thưởng thêm tối đa 8 điểm
- `accommodative_stress` — so biên độ điều tiết với chuẩn Hofstetter 📐 `18.5 − 0.30×tuổi`
- Dự báo control — tuổi nhỏ đáp ứng tốt hơn

**⚙️ Cảnh báo:** chuẩn hoá tuyến tính trong khoảng 6–18 tuổi. Nguy cơ cận thị theo
tuổi gần như chắc chắn **không tuyến tính** — thường dốc nhất ở 8–12 tuổi.

### Sphere / Cylinder — OD và OS 🟢

Cầu và trụ từng mắt, dioptre.

**Dùng để:**
- Tính spherical equivalent 📐 `SE = sphere + cylinder/2`
- `refractive_risk` — độ cận nặng thì rủi ro cao ⚙️ trọng số 0.30
- `interocular_image_balance` — chênh lệch hai mắt (anisometropia) ⚙️ trọng số 0.55
- **Bất đối xứng OD/OS** — mắt cận nặng hơn nhận thiết kế mạnh hơn
- Đơn kính nền trong file gia công (mặt sau)

**📐 Đáng tin:** SE và độ lệch hai mắt là công thức chuẩn.
**⚙️ Chưa chắc:** khoảng chuẩn hoá 1–8 D, và ngưỡng anisometropia 0.25–3.0 D.

### Axis (trục) 🟣

Trục trụ, độ.

**Chỉ dùng cho hình học.** Không đi vào bất kỳ chỉ số nào. Nó xác định hướng của
số hạng trụ trong bản đồ sag gửi cho xưởng gia công mặt sau.

📐 Công thức chuẩn: `cyl_power(θ) = cylinder × sin²(θ − axis)`

*Ghi chú:* đổi axis **không đổi** phenotype hay % dự báo — đúng về mặt y khoa,
nhưng đổi Design ID vì hình học khác đi.

### Axial length — OD và OS 🟢

Chiều dài trục nhãn cầu, mm. **Đây là chỉ số quan trọng nhất của toàn hệ thống.**

**Dùng để:**
- `refractive_risk` — trọng số cao nhất ⚙️ 0.35
- `interocular_image_balance` — lệch trục hai mắt ⚙️ trọng số 0.45
- **Bất đối xứng OD/OS** trong tổng hợp thiết kế
- Chọn tier thiết kế (Low/Medium/High)
- Dự báo mức kiểm soát
- Theo dõi tiến triển ở lần tái khám, và kích hoạt refit

**⚙️ Cảnh báo:** khoảng chuẩn hoá 22.5–26.5 mm, tuyến tính. AL 26.5 mm bị coi là
"rủi ro tối đa" — trên mức đó hệ thống không phân biệt nữa.

### Photopic pupil (đồng tử sáng) 🟢

Đường kính đồng tử trong ánh sáng, mm.

**Dùng để:**
- `visual_stress` — đồng tử lớn thì chói nhiều hơn ⚙️ trọng số 0.15
- `dynamic_robustness` — đồng tử lớn thì kém dung sai hơn
- **Đường kính vi cấu trúc** ⚙️ `22.0 + 10.0 × norm(pupil, 3.0, 6.5) + 0.04 × density` µm

**⚙️ Cảnh báo nặng:** dòng cuối cho ra **kích thước vật lý thật** đi vào file gia
công. Hằng số 22.0 và 10.0 hoàn toàn tự đặt.

---

## Section 2 — Thị giác hai mắt

### Near phoria (lác ẩn gần) 🟢

Prism dioptre, exo mang dấu âm.

**Dùng để:** `binocular_load` ⚙️ trọng số 0.40, đo **độ lệch so với chuẩn Morgan**
📐 (−3Δ exo ở gần), **hai chiều**.

| phoria | binocular_load |
|---|---|
| exo −8Δ | 41.0 |
| **chuẩn −3Δ** | **17.1** ← thấp nhất |
| ortho 0Δ | 31.4 |
| eso +8Δ | 69.5 |

📐 **Đáng tin:** mốc chuẩn Morgan là y văn, và việc đo lệch hai chiều là đúng lâm
sàng — orthophoria ở gần *đã là* một dịch chuyển về phía eso.

**Hướng lệch cũng ảnh hưởng thiết kế** — peripheral add giảm quy tụ do điều tiết
nên giúp eso, hại exo. Đường dẫn đã nối nhưng ⚙️ `sa_vergence_direction_gain = 0`
**chờ thầy chốt độ lớn**.

### NPC — Near Point of Convergence 🟢

Điểm quy tụ gần, cm.

**Dùng để:** `binocular_load` ⚙️ trọng số 0.30, khoảng chuẩn hoá 5–15 cm.

NPC càng xa (lùi ra) thì tải hai mắt càng cao.

---

## Section 3 — Điều tiết

### Accommodative lag (độ trễ điều tiết) 🟢

Dioptre.

**Dùng để:**
- `accommodative_stress` ⚙️ trọng số 0.45, khoảng 0.25–2.0 D
- Chỉ số này **tăng SA** ⚙️ hệ số `+0.12 × accom`

Đây là đường dẫn từ "mắt điều tiết kém khi nhìn gần" tới "thiết kế cần hỗ trợ gần
nhiều hơn".

---

## Section 4 — Tần số không gian

### Contrast sensitivity (CSF) — dải Low/Mid/High 🟡

Dropdown 3 mức. **Đây là fallback, không phải phép đo.**

Dùng khi chưa đo đường cong. Quy thành một số 0–100 rồi vào
`spatial_frequency_sensitivity`, `neural_adaptation`, `dynamic_robustness`.

⚙️ Ba mức quy về **45 / 70 / 90**. Thầy yêu cầu rõ **không được ánh xạ cố định**,
nên ba số này nằm trong config và một đường cong đo được luôn thắng.

Kết quả trả về có `csf_protocol.measured = false` khi chỉ dùng dải — bác sĩ nhìn là
biết con số đến từ ấn tượng lâm sàng chứ không phải máy đo.

> Muốn CSF thật sự tham gia thiết kế thì nhập đường cong ở **Tier 3**.


---

## Section 5 — Thần kinh thị giác

### Visual stress (0–10) 🟢

Điểm bệnh nhân tự báo về khó chịu thị giác.

**Dùng để:**
- `visual_stress` ⚙️ trọng số 0.50
- Suy ra comfort tolerance khi không có Visual comfort score 📐 `comfort = (10 − stress) × 10`
- **Giảm SA** ⚙️ hệ số `−0.18 × stress` — bệnh nhân nhạy cảm nhận thiết kế nhẹ hơn
- Giảm mật độ ⚙️ `−0.20 × stress`
- Tăng jitter ⚙️ `+2.0 × stress`

Đây là một trong những input tác động rộng nhất.

---

## Section 6 — Nhiệm vụ thị giác & môi trường

### Near work (giờ/ngày) 🟢

**Dùng để:** `refractive_risk` ⚙️ 0.15 · `accommodative_stress` ⚙️ 0.30 ·
dự báo control · `task_load` (domain T của phenotype).

### Digital device (giờ/ngày) 🟢

**Dùng để:** `visual_stress` ⚙️ 0.25 · `accommodative_stress` (cộng với near work) ·
`task_load`.

Trong tổng hợp thiết kế nó được tính **nửa trọng số** so với near work:
⚙️ `near_hours + 0.5 × digital_hours`. Hệ số 0.5 tự đặt.

### Outdoor activity (giờ/ngày) 🔵

**Dùng để:** dự báo mức kiểm soát (yếu tố bảo vệ) và `task_load`.

**Lưu ý quan trọng:** thời gian ngoài trời **không đổi thiết kế quang học** — nó
chỉ đổi **dự báo kết quả**. Điều này hợp lý về y khoa (ra ngoài trời là can thiệp
hành vi, không phải can thiệp quang học) nhưng bác sĩ cần biết để không kỳ vọng
Design ID đổi khi sửa ô này.

⚙️ Khoảng chuẩn hoá 0–4 giờ, tuyến tính.

### Primary visual goal 🟢

Dropdown 9 lựa chọn.

**Dùng để:** đổi **trọng số hàm loss** — tức đổi *hệ thống coi trọng gì* khi chọn
trong 3 candidate, không đổi *cách sinh ra thiết kế*.

| Mục tiêu | Ưu tiên cao nhất |
|---|---|
| Myopia Management | control 0.35 |
| Digital Visual Comfort | comfort 0.30, adaptation 0.25 |
| Reading | acuity + comfort + adaptation, mỗi thứ 0.25 |
| Near Work | adaptation 0.30 |
| Presbyopia | acuity 0.35 |
| Driving | acuity 0.30, robustness 0.25 |
| Night Vision | acuity 0.30, robustness 0.30 |
| Sports Vision | robustness 0.35 |
| General Visual Comfort | comfort 0.30, adaptation 0.30 |

**⚙️ Cảnh báo:** *thứ tự* ưu tiên phát biểu được (nhìn đêm cần acuity và độ bền
theo đồng tử), nhưng **độ lớn cụ thể là bịa**.

**Đã mở rộng:** candidate giờ là lưới **SA × density**, 9 phương án mỗi mắt, 81
cặp. Hai trục đánh đổi khác nhau:

| Tăng | control | acuity | comfort |
|---|---|---|---|
| **SA** (cường độ) | ↑ | **↓** | ↓ |
| **Fill factor** (độ phủ) | ↑ | **—** | ↓ |

Nên muốn thêm kiểm soát mà giữ thị lực thì mua bằng **độ phủ**. Ví dụ Reading
chọn đúng phương án SA thấp + fill factor cao.

Kết quả: **4/9 mục tiêu ra thiết kế khác nhau** (trước là 2).

**⚠️ Hạn chế còn lại:** độ phân hoá phụ thuộc *biên độ tương đối* giữa 6 hạng mục
loss, mà các biên độ đó chưa hiệu chỉnh — control trải 18 điểm còn acuity chỉ 4
điểm, nên control áp đảo ngay cả khi trọng số thấp hơn.

> **Lịch sử:** ô này từng **hoàn toàn không có tác dụng** — 9 mục tiêu ra cùng một
> thiết kế. Đã sửa; xem mục "Lỗi input trơ" trong ASSUMPTIONS.md.

---

# TIER 2 — Advanced Clinical Data

Không bắt buộc. Nhập thêm thì tăng độ chính xác và độ tin cậy.

## Thị giác hai mắt

### PFV — Positive Fusional Vergence 🟢

**Dùng để:** `binocular_load` ⚙️ trọng số 0.15, khoảng 8–30Δ. Dự trữ quy tụ thấp
thì tải hai mắt cao.

Cũng bật cờ `binocular_extended` → tăng confidence.

### AC/A ratio 🟢

**Dùng để:** `binocular_load` ⚙️ trọng số 0.15, phạt theo độ lệch khỏi giá trị
chuẩn 4.0 Δ/D.

⚙️ Giá trị "chuẩn" 4.0 và khoảng phạt 0–6 là tự đặt.

### Binocular balance (Normal/Mild/Significant) 🔵

Đánh giá của bác sĩ về khả năng hợp thị.

**Dùng để:** `interocular_image_balance` — **ghi đè** kết quả tính từ số liệu.
⚙️ Normal → 0, Mild → 0.25, Significant → 0.55.

Lý do có ô này: bệnh nhân có thể **đều khúc xạ hai mắt mà vẫn hợp thị kém** — con
số không nói hết.

### BCVA OD / OS (logMAR) 🔵

**Dùng để:** `interocular_image_balance` ⚙️ trọng số 0.20 · và tính
`interocular_acuity_difference` hiển thị cho bác sĩ 📐 `|BCVA_OD − BCVA_OS|`

### NFV — Negative Fusional Vergence 🟢

**Dùng để:** tiêu chuẩn **Sheard** 📐 — dự trữ *đối kháng* phải ≥ 2× độ lệch.
Exo cần PFV, **eso cần NFV**. Hệ thống đọc đúng dự trữ theo chiều lệch.

Đây là tiêu chuẩn y văn, không phải trọng số bịa, nên dùng trực tiếp.

### Stereoacuity (arc sec) 🟢

**Dùng để:** `binocular_load` ⚙️ trọng số 0.10, khoảng 40–400 arcsec.

### Distance phoria 🟢

**Dùng để:** `binocular_load`, đo lệch so với chuẩn 📐 (−1Δ), nhưng ⚙️ **trọng số
chỉ bằng 0.4 lần** near phoria — vì kính này đeo cho công việc nhìn gần, nơi tải
quy tụ thực sự nằm.

### Ocular dominance (OD/OS/Balanced) 🟡

**Đã nối, hệ số = 0.** Mắt chủ đạo gánh nhiều nhiệm vụ thị giác hơn nên có thể xứng
đáng nhận thiết kế nhẹ hơn. Hướng hợp lý, nhưng ⚙️ `dominance_asymmetry_gain = 0`
**chờ quy tắc lâm sàng từ thầy**.

---

## Điều tiết

### Amplitude of accommodation (D) 🟢

**Dùng để:** `accommodative_stress` ⚙️ trọng số 0.15 — so với chuẩn Hofstetter tối
thiểu 📐 `18.5 − 0.30 × tuổi`, sàn 4.0 D.

📐 Công thức Hofstetter là chuẩn y văn. ⚙️ Trọng số 0.15 và khoảng phạt 0–6 D là
tự đặt.

### Accommodative facility (cpm) 🟢

**Dùng để:** `accommodative_stress` ⚙️ trọng số 0.10, khoảng 3–12 cpm.

### Near working distance (cm) 🟢

**Dùng để:** suy ra nhu cầu điều tiết 📐 `demand (D) = 100 / khoảng cách (cm)` —
đây là **định nghĩa**, không phải giả định — rồi đưa vào `accommodative_stress`
⚙️ trọng số 0.20, khoảng quy chuẩn 2–5 D.

Cũng thay cho `typical_working_distance` trong `task_load` khi ô kia bỏ trống.

Giá trị hiện cho bác sĩ ở trường `accommodative_demand_d`.

### Computer working distance (cm) 🟢

**Dùng để:** như trên. Khi nhập cả hai, **khoảng cách gần hơn quyết định** — vì đó
là nhiệm vụ khó hơn mà mắt phải duy trì.

---

## Thần kinh thị giác & môi trường

### Visual comfort score (0–10) 🔵

**Dùng để:** khi có ô này, nó **thay thế** giá trị suy từ Visual stress:
📐 `comfort = score × 10` thay vì `(10 − stress) × 10`.

Từ đó vào `neural_adaptation` và toàn bộ dự báo.

### Neural adaptation score (0–10) 🔵

**Dùng để:** khi có, **thay thế hoàn toàn** công thức tính `neural_adaptation`
nội bộ 📐 `index = score × 10` (rồi cộng thưởng theo tuổi).

### Dynamic visual stability (0–10) 🔵

**Dùng để:** `dynamic_robustness` ⚙️ trọng số 0.55.

**⚙️ Mặc định khi không nhập: 70.** Con số này tự đặt và ảnh hưởng tới ô
DYNAMIC ROBUSTNESS hiển thị cho bác sĩ.

### Mesopic pupil (mm) 🔵

**Dùng để:** khi có, `dynamic_robustness` chuyển sang tính theo **độ giãn**
📐 `mesopic − photopic` thay vì chỉ dùng photopic.

⚙️ Khoảng chuẩn hoá độ giãn 0.5–3.5 mm là tự đặt.

### Typical working distance (cm) 🔵

**Dùng để:** `task_load` ⚙️ trọng số 0.20, khoảng 25–60 cm.

**Chỉ ảnh hưởng domain T của phenotype**, không tới thiết kế.

⚙️ Mặc định khi không nhập: 40 cm.

### Night driving (checkbox) 🟢

**Dùng để:** `visual_stress` ⚙️ +0.10 · `task_load` ⚙️ +0.10.

Từ đó gián tiếp đổi SA, mật độ, jitter.

### Low-light visual demand (Low/Moderate/High) 🟢

**Dùng để:** như Night driving — mức High cộng ⚙️ 0.10 vào `visual_stress` và
`task_load`.

⚙️ Chỉ mức **High** có tác dụng; Low và Moderate hiện như nhau.

---

# TIER 3 — Research Mode

## Đường cong CSF — Tier 3

### Low / Mid / High spatial frequency CSF 🟢

Ba giá trị của đường cong. **Thắng dropdown ở Tier 1.**

### Value scale 🟢

`index_0_100` (thang cũ) hoặc `log_cs` (thứ máy báo trực tiếp).

📐 Ghi lại thang đo là khác biệt giữa một con số so sánh được giữa các phòng khám
và một con số không so sánh được.

### Device / Test protocol 🟣

Chỉ để truy vết — không đi vào tính toán. Nhưng nó theo phép đo vào cơ sở dữ liệu,
nên sau này biết số liệu đến từ máy nào.

### Low / Mid / High frequency (cycles/degree) 🟢

**Tần số thật của máy.** Nếu bỏ trống, hệ thống giả định 1.5 / 6 / 18 cpd và **đánh
dấu `frequencies_assumed: true`** — giả định đi theo dữ liệu chứ không nấp trong code.

📐 AUC và slope tính trên trục log tần số, nên nhập sai tần số là sai cả hai. Slope
lại điều khiển phạt roll-off, nên tần số **thật sự tới được thiết kế**.

Cùng một đường cong, đo ở `[3,6,12]` và `[1,4,16]` cpd cho slope khác nhau — đúng
như phải thế.

## Điện sinh lý & wavefront

### VEP — stimulus, amplitude, latency, interocular difference, Z-score

**Chỉ `vep_z_score` làm tăng độ tin cậy** 🟢. Bốn trường còn lại 🟡 được ghi lại cho
dữ liệu huấn luyện sau này.

Lý do, theo đúng lời thầy: *"confidence should not be mechanically increased simply
because a VEP test was performed."* Một con số trần không có stimulus, đơn vị hay
mốc tham chiếu — **nó là bản ghi, không phải bằng chứng**.

| Nhập gì | Confidence |
|---|---|
| không gì | 75 |
| `vep = 1.5` (số trần) | **75** — ghi lại, không tính |
| `vep_z_score = −1.2` | 80 |

Kết quả mang `neurovisual_status`: `not_recorded` / `recorded_not_interpretable` /
`interpretable` — bác sĩ đã đo VEP thì xứng đáng biết cái nào đã xảy ra.

### ERG — protocol + Z-score

Như VEP: chỉ `erg_z_score` được tính.

### Eye tracking — năm chỉ số con

🟢 `fixation_stability` · `blink_rate` · `vergence_stability`
🟡 `pupil_dynamics` · `gaze_distribution` (ghi lại, chưa dùng)

Thầy nhận định đây là **nhóm có tiềm năng vào optimizer sớm nhất**. Cả năm đã là
feature, sẵn dữ liệu cho lúc đó.

### HOA RMS / Coma / Trefoil 🟢

**Dùng để:** `residual_aberration_load` → **giảm `neural_adaptation`**
⚙️ tối đa −15 điểm.

Logic: mắt có ảnh võng mạc đã kém sẵn thì thích nghi với tải quang học thêm khó hơn.
Hướng và độ lớn đại khái đều không gây tranh cãi, nên hệ số khác 0.

**Cũng đã nối vào SA** qua ⚙️ `sa_hoa_tolerance_gain = 0` — chờ thầy.

### Corneal spherical aberration 🟢/🟡

**Dùng để:** `corneal_sa_departure` = SA đo được − SA mắt trung bình (0.27 µm).

**Đây là đường quan trọng nhất trong nhóm:** bệnh nhân đã sẵn SA giác mạc dương thì
kính **không nên cộng thêm bấy nhiêu**. Đường dẫn đã nối vào tổng hợp SA, nhưng
⚙️ `sa_corneal_compensation = 0` — vì **cộng sai lượng SA còn tệ hơn không cộng**.

Có test chứng minh chỉ cần điền số là hoạt động: đặt 1.0 thì SA giác mạc 0.55 µm →
SA kính giảm từ 6.15 xuống 4.75 D.

### Corneal astigmatism 🟢

**Dùng để:** 📐 `residual_astigmatism = |trụ khúc xạ| − loạn giác mạc` — đây là
**định nghĩa**. Loạn dư (thành phần thể thuỷ tinh) làm giảm chất lượng ảnh mà thiết
kế phải làm việc cùng, nên đi vào `residual_aberration_load`.

### Corneal eccentricity 🟡

**Đã nối, hệ số = 0.** Giác mạc prolate hơn thì **tự nó đã tạo viễn thị ngoại vi
tương đối** — đúng tín hiệu mà kính đang cố tạo ra. Nên kính phải *bổ sung* chứ
không *lặp lại*. ⚙️ `sa_corneal_asphericity_gain = 0` chờ thầy.

---

# Tổng kết theo mức độ ảnh hưởng

## 🟡 Hai ô đã nối nhưng hệ số = 0

| Ô | Cần gì |
|---|---|
| Ocular dominance | Quy tắc: mắt chủ đạo lệch thiết kế bao nhiêu |
| Corneal eccentricity | Quy tắc quang học: giác mạc prolate bù bao nhiêu |

Cùng nhóm này còn ba **hệ số** đã nối nhưng để 0 trong `config.py`:
`sa_vergence_direction_gain`, `sa_corneal_compensation`, `sa_hoa_tolerance_gain`.

Tất cả đều theo một nguyên tắc: **hướng thì phát biểu được, độ lớn thì không được
bịa**. Đường dẫn đã nối và có test — thầy điền một số là xong, không phải sửa code.

Hai ô trên được đánh dấu **`NOT YET USED`** trên giao diện.

## 🟡 Ba ô vẫn chỉ tăng confidence

VEP · ERG · Eye tracking — **vì không biết đơn vị**. Không thể nối một đại lượng
mà không biết chiều tăng của nó nghĩa là gì.

Có test `test_no_new_inert_clinical_inputs` chặn việc thêm ô mới mà quên nối.

## 🟡 Chín ô chỉ tăng confidence

NFV · Stereoacuity · VEP · ERG · Eye tracking · HOA RMS · Corneal SA · Coma · Trefoil

**Đã sửa một nửa.** Confidence giờ nhìn cả giá trị:

```
confidence = 55 + 25 × coverage + 20 × plausibility
```

- Giá trị **ngoài khoảng bình thường** trừ điểm, và **không được tính là đã đo**
  nhóm đó — vì nó không cho biết gì đáng tin.
- Kết quả trả về `out_of_range_measurements` nêu **đích danh** giá trị nào bất
  thường, và phần "Why this design" nói rõ dự báo đang là ngoại suy.

| Trường hợp | Confidence |
|---|---|
| Không đo thêm | 75 |
| Đo 1 giá trị hợp lý | 80 |
| **Đo 1 giá trị bất thường** | **70** |

**Nửa còn lại chưa sửa:** 4 ô wavefront vẫn không ảnh hưởng thiết kế. Quang sai
sẵn có của bệnh nhân đáng lẽ phải quyết định lượng SA kính đưa vào. Cần quy tắc
quang học từ thầy.

⚙️ 33 khoảng "hợp lý" và hình phạt 0.25 mỗi vi phạm đều là số tự đặt.

## 🟢 Ảnh hưởng tới thiết kế thật

Age · Sphere · Cylinder · Axial length · Photopic pupil · Near phoria · NPC ·
Accommodative lag · CSF band · Visual stress · Near hours · Digital hours ·
Primary goal · PFV · AC/A · Amplitude of accommodation · Accommodative facility ·
Night driving · Low-light demand · CSF triplet

---

# Ba cảnh báo tổng thể

## 1. Không có hệ số nào được hiệu chỉnh lâm sàng

Mọi trọng số trong tài liệu này (mọi chỗ có ⚙️) đều do con người đặt ra cho "trông
hợp lý". Chúng đúng về **hướng** — tăng chiều dài trục thì rủi ro tăng — nhưng
**không đúng về độ lớn**.

Không có mô hình nào được huấn luyện trên dữ liệu bệnh nhân thật.

## 2. Hàm ánh xạ phenotype → thiết kế là phần đáng ngờ nhất

Sáu dòng trong `nso/design/synthesis.py` biến hồ sơ bệnh nhân thành kích thước vi
cấu trúc thật. Đây vừa là **tài sản trí tuệ cốt lõi** mà cả kiến trúc bảo mật đang
bảo vệ, vừa là phần **ít cơ sở nhất**.

Cụ thể, các dòng cho ra **kích thước vật lý sẽ đi vào file gia công**:

```python
diameter    = 22.0 + 10.0 × norm(pupil, 3.0, 6.5) + 0.04 × density   µm
height      = 0.22 × SA + 0.6 × (1 − CSF) + 0.9                      µm
fill_factor = 20.0 + 0.28 × density − 8.0 × (1 − CSF)                %
jitter      = 4.0 + 8.0 × (1 − CSF) + 2.0 × stress                   độ
```

## 3. Thiếu dữ liệu cấu tạo tròng kính

Bốn hằng số quyết định toàn bộ hình học gửi nhà máy — chiết suất 1.60, đường kính
40 mm, base curve 4.0 D, bán kính tham chiếu 10 mm — **chưa từng được ai cung cấp**.

Xem [ASSUMPTIONS.md](ASSUMPTIONS.md) mục P0-1.

---

# Câu hỏi cần thầy trả lời

Xếp theo mức độ ảnh hưởng:

1. **Hệ số ánh xạ phenotype → thiết kế** — sáu dòng ở trên. Đây là câu hỏi quan
   trọng nhất của cả dự án.
2. **Spec cấu tạo tròng kính** — chiết suất, đường kính, base curve, bán kính
   tham chiếu.
3. **Quang sai bậc cao của bệnh nhân nên ảnh hưởng SA thiết kế thế nào?** — hiện
   4 ô wavefront chỉ tăng confidence.
4. **Máy đo CSF dùng tần số nào, thang gì?** — quyết định AUC và slope.
5. **Phoria xa và stereoacuity nên vào công thức với trọng số nào?**
6. **Mắt chủ đạo ảnh hưởng bất đối xứng OD/OS ra sao?**
7. **Bệnh nhân eso (lác trong) xử lý thế nào?** — hiện bị coi như phoria = 0.
