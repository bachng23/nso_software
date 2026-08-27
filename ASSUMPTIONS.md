# Giả định, hằng số bịa, và những chỗ chưa chắc chắn

> **Cập nhật 2026-08-24 — V2.1 Design Baseline.** Thầy đã trả lời phần lớn câu hỏi
> và cung cấp **số liệu thật đầu tiên**: bảng hình học ba vùng A/B/C, mục tiêu
> quang học từng tier, đường kính kính 65 mm, hệ số truyền quy trình (3.0 → 1.8 µm),
> và các khoảng modifier có biên. Những mục dưới đây đã được cập nhật theo.
>
> Điều quan trọng nhất thầy nói: *"381 test pass nghĩa là **phần mềm** V2 đã xong,
> nhưng **mô hình quang học** mới ở khoảng V0.5."*

Tài liệu này liệt kê **mọi con số trong hệ thống không đến từ dữ liệu thật**, và mọi
chỗ code đang đoán. Viết ra để không ai — kể cả người viết code — nhầm tưởng một
con số nào đó đã được kiểm chứng.

Nguyên tắc đọc: **engine hiện tại là rule-based, không có mô hình nào được huấn
luyện trên dữ liệu lâm sàng.** Mọi trọng số đều do con người đặt ra cho "trông hợp
lý". Chúng đúng về mặt *hướng* (tăng cái này thì cái kia tăng/giảm), nhưng **không
đúng về mặt độ lớn**.

Phân mức:

| Mức | Nghĩa |
|---|---|
| 🔴 **P0** | Sai số này có thể ra tới bệnh nhân hoặc nhà máy. Phải có dữ liệu thật trước khi dùng thật. |
| 🟠 **P1** | Cần hiệu chỉnh lâm sàng. Ảnh hưởng thiết kế được chọn. |
| 🟡 **P2** | Vấn đề kỹ thuật/vận hành, không phải y khoa. |
| ⚪ **P3** | Dọn dẹp, không ảnh hưởng kết quả. |

---

## 🔴 P0 — Chặn trước khi dùng thật

### P0-1. Hằng số cấu tạo tròng kính — 🟡 MỘT PHẦN ĐÃ CÓ SỐ THẬT

✅ **Đường kính kính = 65 mm** — thầy cho (`r = 0 → 32.5 mm`). Trước để 40 mm.

❌ Còn lại vẫn bịa: chiết suất 1.60, base curve 4.0 D, bán kính vùng chức năng 10 mm.

<details><summary>Nội dung cũ</summary>

### P0-1. Hằng số cấu tạo tròng kính hoàn toàn là bịa

[`nso/config.py`](src/backend/nso/config.py) — nhóm *Lens construction*

```python
LENS_INDEX = 1.60
OPTIC_ZONE_DIAMETER_MM = 40.0
BASE_CURVE_D = 4.0
SA_REFERENCE_SEMI_DIAMETER_MM = 10.0
```

Bốn con số này **chưa từng được ai cung cấp**. Mình chọn cho "trông giống tròng
kính thật".

Hậu quả: chúng quyết định **toàn bộ hình học** gửi cho nhà máy.
- `LENS_INDEX` sai → bán kính cong sai → sai công suất
- `OPTIC_ZONE_DIAMETER_MM` sai → sai kích thước phôi và số lượng vi cấu trúc
- `SA_REFERENCE_SEMI_DIAMETER_MM` sai → sai độ lớn số hạng bậc cao

**Cần**: spec thật của tròng kính NSO.
</details>

### ~~P0-2. Quy ước SA → sag~~ ✅ CÂU HỎI ĐÃ ĐƯỢC TRẢ LỜI

Thầy làm rõ: **SA mặt nền và điều biến vi cấu trúc là HAI KÊNH QUANG HỌC RIÊNG**,
không được gộp.

| Kênh | Nội dung |
|---|---|
| Mặt sau/nền | đơn kính · freeform · **tuỳ chọn** aspheric/SA bậc thấp |
| Vi cấu trúc mặt trước | điều biến theo thống kê không gian · tán xạ/pha · blue-noise/Poisson · bất đối xứng thái dương |

Và chỉ đích danh lỗi cũ: *"nếu coi hiệu ứng quang học của vi cấu trúc NSO cũng là
SA sag thì **sai**."*

Đã tách thành `BaseSurfaceProfile` và `NsoModulationProfile`. `surface_map()` giờ
chỉ đọc `base_surface.aspheric_sa_d` (mặc định 0), **không bao giờ** đọc mục tiêu
vi cấu trúc. Có test khẳng định đổi vi cấu trúc không làm nhúc nhích bản đồ sag.

<details><summary>Nội dung cũ</summary>

### P0-2. Quy ước chuyển "SA strength (D)" → sag là mình tự đặt

[`nso/manufacturing/geometry.py`](src/backend/nso/manufacturing/geometry.py) — `surface_map()`

Recipe lưu SA dưới dạng **dioptre**. Muốn xuất hình học phải chuyển sang **sag
(mm)**. Không có tài liệu nào nói chuyển thế nào, nên mình đặt ra:

> peripheral add `sa_strength` D tại bán kính `r0` → số hạng `k·r⁴` sao cho sag
> thêm vào tại `r0` khớp với add đó.

Đây **không phải công thức chuẩn ngành**. Nó tự nhất quán, không đảm bảo đúng.
</details>

### P0-3. Roll-off của số hạng bậc cao là placeholder thuần tuý

[`nso/manufacturing/geometry.py`](src/backend/nso/manufacturing/geometry.py) — `r_ho = min(r, r0)`

Lúc đầu để `r⁴` chạy tự do tới rìa kính → sag vọt lên **~5 mm**, vô lý (tròng kính
thật cỡ dưới 1 mm). Mình cắt phẳng ngoài `r0`, sag còn −0.91 … +0.07 mm.

🟡 **ĐÃ CẢI THIỆN, CHƯA GIẢI QUYẾT.**

Cắt phẳng tạo gãy đạo hàm bậc 2 tại `r0` — một vòng tròn khuyết tật quang học ở
đúng bán kính 10 mm. Đã thay bằng **blend smootherstep** (`6t⁵−15t⁴+10t³`), liên
tục tới đạo hàm bậc 2 ở cả hai đầu.

Đo trước/sau, đạo hàm bậc 2 quanh r₀:

| r (mm) | clamp cũ | blend mới |
|---|---|---|
| 9.99 | 0.0399 | 0.0399 |
| 10.01 | **0.0000** ← nhảy | 0.0400 |
| 10.50 | 0.0000 | −0.0064 |
| 14.50 | 0.0000 | 0.0000 |

Sag vẫn trong khoảng hợp lý (< 3 mm). Độ rộng blend `sa_rolloff_blend_mm = 4.0`
là **số bịa**.

✅ **THẦY ĐÃ CHỌN: phương án (b)+(c) kết hợp.**

Số hạng bậc cao chỉ tồn tại **trong vùng quang học chức năng xác định**, chuyển
tiếp trơn ở biên vùng. Không được để một đa thức r⁴ chạy tự do tới rìa phôi 65 mm.

```
SA_effective(r) = C₄·r⁴ · W(r)     với W(r) là cửa sổ có biên
```

Đã cài `_window()` dùng smootherstep. Blend 4 mm **giữ lại như mặc định kỹ thuật**,
đúng lời thầy: *"a 4 mm smooth blend can be used as an engineering default rather
than as a fixed NSO physical constant"*.

⚠️ Độ rộng thật vẫn cần xác định từ MTF/PSF + khả năng gia công freeform.

### P0-4. Dung sai nghiệm thu là số bịa

[`nso/config.py`](src/backend/nso/config.py) — nhóm *Acceptance windows*

```python
SAG_TOLERANCE_MM = 0.002
HEIGHT_TOLERANCE_MM = 0.0002
POSITION_TOLERANCE_MM = 0.005
DECENTRATION_TOLERANCE_MM = 0.25
```

Đây là **ngưỡng đạt/không đạt gửi cho xưởng**, và là tiêu chí `optical_verification()`
dùng để phán Pass/Fail. Chưa hỏi xưởng nào.

Nguy hiểm hai chiều: quá chặt → loại oan hàng tốt; quá lỏng → cho qua hàng lỗi.

**Cần**: process capability của xưởng thật.

### P0-5. Verification **không** kiểm tra quang học

[`nso/manufacturing/verification.py`](src/backend/nso/manufacturing/verification.py)

🟡 **ĐÃ LÀM RÕ, CHƯA GIẢI QUYẾT.**

Tên hàm cũ `optical_verification` **nói dối** — nó chỉ so hình học (sai số sag,
chiều cao, vị trí, lệch tâm), không đo MTF hay through-focus. Đây là kiểu sai tệ
nhất: người đọc không có lý do gì để nghi ngờ.

Đã đổi thành `geometric_verification`, và mọi kết quả trả về giờ mang:

```json
"verification_type": "geometric",
"optical_performance_verified": false,
"optical_performance_note": "Geometric conformance only. MTF and through-focus
performance were not measured; a bench protocol has not been defined."
```

Tên cũ vẫn dùng được qua shim `nso_v2.py`, có ghi chú lý do đổi.

**Vẫn cần**: protocol đo trên bench + spec thiết bị. Một tròng kính **Pass** vẫn
có thể không đạt hiệu năng quang học dự đoán — chỉ khác là bây giờ payload nói ra
điều đó.

### P0-6. Ngưỡng fusion đôi mắt quyết định thiết kế nhưng không có cơ sở

[`nso/config.py`](src/backend/nso/config.py) — nhóm *Joint binocular optimization*

```python
BINOCULAR_WEIGHT = 0.30
SA_FUSION_TOLERANCE_D = 0.4
DENSITY_FUSION_TOLERANCE = 12.0
```

Đây là ba số **quyết định cặp OD/OS nào được chọn** trong joint optimization. Giới
hạn aniseikonia/fusion thật phải đến từ dữ liệu lâm sàng.

Đổi `SA_FUSION_TOLERANCE_D` từ 0.4 → 0.8 là bệnh nhân nhận thiết kế khác.

---

## 🟠 P1 — Cần hiệu chỉnh lâm sàng

### P1-1. Toàn bộ trọng số của 8 chỉ số AI

[`nso/features.py`](src/backend/nso/features.py)

Ví dụ `refractive_risk_index`:

```python
0.35 * norm(al, 22.5, 26.5)
+ 0.30 * norm(abs(se), 1.0, 8.0)
+ 0.20 * (1 - norm(p.age, 6, 18))
+ 0.15 * norm(p.near_hours, 1, 12)
```

Tại sao 0.35 mà không phải 0.30? **Không có lý do.** Chọn để tổng bằng 1 và thứ tự
quan trọng trông hợp lý. Điều này đúng cho **cả 8 chỉ số**.

Các khoảng chuẩn hoá (`22.5–26.5 mm`, `1–8 D`, `6–18 tuổi`…) là khoảng lâm sàng hợp
lý, nhưng việc **tuyến tính hoá** trong khoảng đó là giả định — rủi ro cận thị theo
chiều dài trục gần như chắc chắn không tuyến tính.

### P1-2. Ngưỡng phân hạng phenotype 40 / 70

[`nso/phenotype.py`](src/backend/nso/phenotype.py) — `grade()`, ngưỡng trong `config.py`

```python
if score < 40: return 1
if score < 70: return 2
return 3
```

Chia ba khoảng bằng mắt. Không có gì đảm bảo 40 và 70 là ranh giới lâm sàng có ý nghĩa.
Ảnh hưởng trực tiếp lên mã phenotype hiện cho bác sĩ (`R2-B1-S1-N2-T2`).

### P1-3. Ba dải CSF quy về ba con số cố định

[`nso/config.py`](src/backend/nso/config.py) — `csf_bands`

```python
CSF_BANDS = {"Low": 45.0, "Mid": 70.0, "High": 90.0}
```

Bác sĩ ở Tier 1 chỉ chọn Low/Mid/High, hệ thống quy thành 45/70/90 trên thang 0–100.
Thang 0–100 này **cũng không phải đơn vị CSF thật** (log contrast sensitivity).

### ~~P1-4. Tần số CSF giả định~~ ✅ ĐÃ SỬA THEO ĐÚNG YÊU CẦU

Thầy xác nhận cách tiếp cận đúng và **yêu cầu đi xa hơn**: đừng ánh xạ cố định
Low/Mid/High thành 45/70/90, và lưu đủ schema.

Module mới `nso/csf.py` với `CsfMeasurement` lưu:

```
device · test_protocol · spatial_frequency_cpd[] · raw_sensitivity[]
logCS[] · normalization_reference · AUC · slope · centroid
```

- Tần số **đi kèm từng phép đo**, không phải hằng số toàn cục
- Nếu phòng khám không báo tần số → đánh dấu `frequencies_assumed: True`, giả định
  đi theo dữ liệu chứ không nấp trong engine
- Low/Mid/High thành **fallback**, và `csf_is_measured` phân biệt rõ
- Slope dùng bình phương tối thiểu trên **mọi** điểm, nên protocol 5+ tần số dùng hết
- Slope (có tính tới tần số) giờ điều khiển phạt roll-off, thay cho hiệu hai giá trị thô

Kết quả trả về mang `csf_protocol` để bác sĩ biết số liệu đến từ thiết bị nào.

<details><summary>Nội dung cũ</summary>

### P1-4. Tần số không gian giả định 1.5 / 6 / 18 cpd

[`nso/config.py`](src/backend/nso/config.py) — `csf_frequencies_cpd`

```python
CSF_FREQUENCIES_CPD = (1.5, 6.0, 18.0)
```

Máy đo CSF của phòng khám dùng tần số nào — **chưa biết**. AUC và slope tính trên
trục log của ba tần số này; đổi tần số là đổi hết kết quả.

Ngoài ra AUC "chuẩn hoá về 0–100 so với đường phẳng 100" là quy ước mình tự đặt cho
số đọc dễ, không phải định nghĩa AUC chuẩn của CSF.
</details>

### P1-5. Hằng số biến phenotype → thiết kế — 🟡 ĐÃ TÁI CẤU TRÚC, CHƯA HIỆU CHỈNH

Thầy nói bốn công thức cũ **không được dùng làm mô hình vật lý NSO chính thức** —
không phải vì sai hướng, mà vì chúng **gộp hai tầng** làm một: "phenotype lâm sàng
→ mục tiêu quang học" và "mục tiêu quang học → hình học gia công".

Đã tách theo đúng kiến trúc thầy chỉ định:

```
Patient phenotype → Optical Modulation Target → NSO Profile → Geometry Projection
```

Tầng lâm sàng giờ **chỉ** xuất ra biến chuẩn hoá 0–1: `control_demand`,
`visual_tolerance`, `contrast_reserve`, `peripheral_modulation`,
`temporal_asymmetry`. Design Engine mới ánh xạ chúng sang D/H/FF của vùng A/B/C.

Lý do thầy nêu, và là lý do đáng giá nhất: đổi sang **MR-8, PC, kính áp tròng hay
đổi xưởng** thì tầng AI lâm sàng **không phải viết lại**.

⚠️ Trọng số trong tầng lâm sàng **vẫn chưa hiệu chỉnh**. Cái đã đổi là chúng sinh
ra một vector nhu cầu độc lập sản phẩm chứ không phải micromét — nên hiệu chỉnh lại
không làm hỏng tầng hình học.

<details><summary>Công thức cũ (đã bỏ)</summary>

### P1-5 (cũ). Hằng số biến phenotype → thiết kế

[`nso/design/synthesis.py`](src/backend/nso/design/synthesis.py), hệ số trong [`nso/config.py`](src/backend/nso/config.py) nhóm *Design synthesis coefficients*

```python
sa = profile["sa_strength"] * (1.0 + 0.12*accom - 0.18*stress + 0.90*asymmetry + 0.25*progression_load) + sa_bias
density = profile["density_numeric"] * (1.0 - 0.20*stress + 0.10*bino + 0.50*asymmetry)
diameter = 22.0 + 10.0 * norm(pupil, 3.0, 6.5) + 0.04 * density
height   = 0.22*sa + 0.6*(1.0 - csf_q) + 0.9
fill_factor = 20.0 + 0.28*density - 8.0*(1.0 - csf_q)
jitter   = 4.0 + 8.0*(1.0 - csf_q) + 2.0*stress
```

**Đây là trái tim của sản phẩm** — hàm ánh xạ từ hồ sơ bệnh nhân sang thiết kế
quang học, thứ mà toàn bộ kiến trúc bảo mật đang bảo vệ.

Và **mọi hệ số trong đó do mình đặt ra.** Không có nghiên cứu nào nói vi cấu trúc
phải cao `0.22 × SA + 0.6 × (1−CSF) + 0.9` µm.

Ba dòng cuối (`diameter`, `height`, `fill_factor`, `jitter`) đặc biệt đáng ngờ vì
chúng cho ra **kích thước vật lý thật** sẽ đi vào file gia công.
</details>

### P1-6. Escalation khi refit

[`nso/config.py`](src/backend/nso/config.py) — `refit_escalation`

```python
REFIT_ESCALATION = {"Controlled": 0.0, "Borderline": 0.35, "Progressing": 0.80}
```

Tiến triển nhanh thì tăng cường độ thiết kế — **hướng thì đúng, độ lớn thì bịa**.
0.80 làm SA tăng ~20%. Con số 20% đó không dựa trên gì.

### P1-7. Ngưỡng dải tiến triển 0.10 / 0.20 mm/năm

[`nso_core.py:152`](src/backend/nso_core.py#L152) và [`nso/config.py`](src/backend/nso/config.py) — `progression_*_ceiling`

Hai ngưỡng này **có cơ sở y văn hơn phần còn lại** (0.1 mm/năm thường được coi là
kiểm soát tốt), nhưng vẫn nên được thầy xác nhận, và chúng bị **lặp ở hai chỗ**
(xem P2-4).

### P1-8. NSO_PROFILES là bảng bịa từ v0.3

[`nso_core.py:28-64`](src/backend/nso_core.py#L28)

```python
"Medium": {"SA": "3-5-4D", "temporal": 1.25, "density_numeric": 66,
           "sa_strength": 5.0, "mtf_reduction": 0.25, "control_power": 0.55, ...}
```

`control_power = 0.55` nghĩa là "profile này kiểm soát được 55%". Con số này đi
thẳng vào `control_score()` và ra tới ô **MYOPIA CONTROL 53%** bác sĩ nhìn thấy.

Chuỗi `"3-5-4D"`, `"5-8-6D"` là ký hiệu thiết kế — cần xác nhận có phải ký hiệu
thật của thầy không, hay chỉ là ví dụ trong tài liệu.

### P1-9. Công thức % giảm chiều dài trục

[`nso_core.py:614`](src/backend/nso_core.py#L614)

```python
def expected_al_reduction_mm(control, baseline_progression=0.30):
    fraction = min(control/100, 1.0) * 0.75
    return baseline_progression * fraction
```

Giả định: chưa điều trị tiến triển 0.30 mm/năm, thiết kế mạnh nhất giảm được 75%.
Cả hai con số đều là giả định.

### P1-10. Ngưỡng trong "Why this design"

[`nso/clinical.py`](src/backend/nso/clinical.py) — `_explainable_summary()`

Các câu giải thích bật khi chỉ số vượt 60 / 60 / 55 / 55 / 55. Bốn ngưỡng đặt bằng
mắt. Chúng quyết định **bác sĩ đọc được lý do gì**, nên sai ngưỡng là giải thích
thiếu hoặc thừa.

### ~~P1-11. Công thức confidence chỉ đếm, không nhìn giá trị~~ ✅ ĐÃ SỬA

Confidence giờ có **hai nửa**:

```
confidence = 55 + 25 × coverage + 20 × plausibility
```

- **coverage** — bao nhiêu nhóm đã đo, và **chỉ tính nhóm có giá trị hợp lý**
- **plausibility** — mỗi giá trị ngoài khoảng bình thường trừ 0.25

`plausible_ranges` trong config có 33 khoảng lâm sàng. Kết quả trả về
`out_of_range_measurements` **nêu đích danh** giá trị nào bất thường, và
`explainable_summary` nói rõ dự báo đang là ngoại suy.

Đo lại:

| Trường hợp | Confidence |
|---|---|
| Không đo thêm | 75 |
| Đo 1 giá trị hợp lý | 80 |
| **Đo 1 giá trị bất thường** | **70** ← thấp hơn cả không đo |
| Đo 2 giá trị bất thường | 65 |

Điểm mấu chốt: lúc đầu sửa xong vẫn hoà — giá trị bất thường vừa tăng coverage
vừa giảm plausibility, đúng bằng nhau. Phải sửa tận gốc: **giá trị ngoài khoảng
không được tính là đã đo nhóm đó**, vì nó không cho biết gì đáng tin.

⚠️ 33 khoảng này là khoảng lâm sàng thông thường, **không phải phân bố huấn luyện**
của mô hình. Khi có model thật phải thay bằng phân bố thật (P1-15).

<details><summary>Nội dung cũ</summary>

### P1-11. Công thức confidence

[`nso/config.py`](src/backend/nso/config.py) — `confidence_floor` / `confidence_span`

```python
confidence = _pct(62 + 30 * coverage)
```

Nhập đủ Tier 1 → 62%. Nhập hết mọi thứ → 92%. **Hai con số 62 và 30 là bịa**, và
công thức chỉ đếm *có đo hay không*, không quan tâm *đo ra giá trị gì*. Một bệnh
nhân với dữ liệu bất thường vẫn được confidence cao y hệt.

Ngưỡng follow-up 6 tháng / 3 tháng đặt ở `confidence >= 80` (`confidence_for_long_followup` trong [`nso/config.py`](src/backend/nso/config.py))
cũng bịa.
</details>

### P1-12. Trọng số hàm loss

[`nso/design/candidates.py`](src/backend/nso/design/candidates.py), trọng số trong [`nso/config.py`](src/backend/nso/config.py) — `loss_weights`

```python
0.35*(100-control) + 0.20*(100-acuity) + 0.15*(100-comfort)
+ 0.15*(100-adaptation) + 0.10*(100-robustness) + 0.05*(100-manufacturability)
```

Ràng buộc cứng `acuity >= 40`, `adaptation >= 35`, `manufacturability >= 30`,
`1.5 <= sa <= 9.0` — **tất cả đều bịa**. Đây là ngưỡng loại một thiết kế khỏi vòng
xét, tức có thể loại oan phương án tốt.

---

## 🟡 P2 — Kỹ thuật / vận hành

### P2-1. Registry nằm trong RAM — restart là mất hết

[`nso/manufacturing/registry.py`](src/backend/nso/manufacturing/registry.py) — `InMemoryDesignStore`

Mọi Design ID lưu trong `dict` của process. Server restart (Render free tier
**cold-start sau ~30 phút không dùng**) → toàn bộ thiết kế biến mất.

Hậu quả thật:
- Bác sĩ tạo thiết kế hôm nay, mai bấm "Submit to Manufacturing" → **404**
- Xưởng kéo gói gia công → **404**
- Refit từ lần khám trước → **404**

**Đây là lỗi chặn deploy thật**, không phải chuyện nhỏ. Cần database.

✅ **ĐÃ SỬA.** Có `SQLiteDesignStore` và `PostgresDesignStore` dùng chung một
schema và một bộ câu lệnh SQL; chọn bằng `NSO_DATABASE_URL`. Đã kiểm bằng
subprocess thật: thiết kế tạo ở process 1 dùng được ở process 2, và server
restart rồi submit lại vẫn **200** thay vì 404. `/api/health` báo backend đang chạy.

Ba chỗ trước đây **sửa dict tại chỗ** (`entry.setdefault(...)`) đã chuyển sang
ghi lại tường minh qua `record_job` / `record_verification` / `link_revision` —
với database thì sửa tại chỗ sẽ im lặng mất dữ liệu.

⚠️ **Hai lưu ý còn lại:**
- **Render free tier có disk ephemeral** — file SQLite không sống qua redeploy.
  Free tier cần Postgres ngoài; Disk trả phí hoặc managed Postgres mới là lời giải thật.
- `PostgresDesignStore` **chưa chạy thử với server thật** (chưa có instance).
  SQL dùng chung với SQLite đã test đầy đủ, phần chưa kiểm là kết nối và
  paramstyle. Phải chạy bộ test store với instance thật trước khi tin.

### ~~P2-2. Số serial job reset mỗi lần restart~~ ✅ ĐÃ SỬA

Serial lấy từ bảng `counters` trong database bằng `UPDATE ... RETURNING` (nguyên
tử trên cả hai backend), nên hai process hoặc hai worker không thể nhận cùng một
số. Đã kiểm: restart server, serial chạy tiếp `8721 → 8723` thay vì quay về 8721.

<details><summary>Nội dung cũ</summary>

### P2-2. Số serial job bắt đầu từ 8721 và reset mỗi lần restart

[`nso/config.py`](src/backend/nso/config.py) — `job_serial_start`

8721 lấy từ ví dụ của thầy (`NSO-26-SG-008721-R03`). Restart → lại từ 8721 → **trùng
job ID**. Với mã số gửi nhà máy thì trùng là nghiêm trọng.
</details>

### ~~P2-3. Một API key dùng chung cho tất cả nhà máy~~ ✅ ĐÃ SỬA

Giờ mỗi vendor role có key riêng (`MANUFACTURING_KEY_FRONT_SURFACE`,
`_BACK_SURFACE`, `_ASSEMBLY`), và `VENDOR_SEGMENTS` giới hạn segment nào mỗi role
được kéo. Key sai segment trả **403**. Có test khẳng định *không key ngoài nào lấy
được quá một segment*.

<details><summary>Nội dung cũ</summary>


[`api.py:50`](src/backend/api.py#L50) — `MANUFACTURING_API_KEY`

Toàn bộ ý nghĩa của segmentation là **không xưởng nào lấy được trọn bộ**. Nhưng hiện
tại một key mở được cả `FS`, `BS` lẫn `AV`.

**Điều này vô hiệu hoá phần lớn P0 của kiến trúc bảo mật.** Xưởng nào có key cũng
kéo được cả ba gói. Cần key riêng cho từng vendor, ràng buộc segment nào được phép.

</details>

### ~~P2-4. Ngưỡng đối xứng OD/OS bị lặp~~ ✅ ĐÃ SỬA

`pair_class()` giờ đọc `CONFIG.sa_fusion_tolerance_d` và
`CONFIG.density_fusion_tolerance` — cùng hằng số optimizer dùng, nên nhãn và
penalty không thể mâu thuẫn. Ngưỡng `1.2` thành `CONFIG.mild_sa_ceiling`.

<details><summary>Nội dung cũ</summary>


[`nso/design/joint.py`](src/backend/nso/design/joint.py) — `pair_class()`

```python
if delta_sa < 0.4 and delta_density < 12:      # số cứng
```

Trong khi [`nso/config.py`](src/backend/nso/config.py) đã có
`SA_FUSION_TOLERANCE_D = 0.4` và `DENSITY_FUSION_TOLERANCE = 12.0`.

Cùng ý nghĩa, hai nơi. Sửa một chỗ quên chỗ kia → nhãn "Symmetric" hiện cho bác sĩ
mâu thuẫn với penalty mà optimizer thực sự tính. Ngưỡng `1.2` ở dòng dưới thì không
có hằng số nào tương ứng cả.

</details>

### ~~P2-5. Phân trang O(n²)~~ ✅ ĐÃ SỬA

Phân trang giờ theo **dải hàng lưới** thay vì theo chỉ số phần tử, nên trang N bắt
đầu ở hàng đã biết, không phải duyệt lại từ đầu.

Đo lại: **93 giây → 0.95 giây** cho toàn bộ; truy cập ngẫu nhiên trang cuối
**0.6 ms**. Có test chặn hồi quy về bậc hai.

<details><summary>Nội dung cũ</summary>


Đo thật: 678,926 phần tử, 340 trang, **93 giây**.

[`nso/manufacturing/geometry.py`](src/backend/nso/manufacturing/geometry.py) — mỗi lần gọi `microstructure_map(page=N)`
duyệt lại **từ đầu** rồi vứt đi N×2000 phần tử. Trang 339 phải sinh 678k phần tử để
trả về 2000.

Xưởng kéo trọn một thiết kế mất ~1.5 phút CPU server, ×2 mắt.

Sửa: sinh theo tile toạ độ thay vì đếm tuần tự, hoặc sinh một lần rồi cache.

</details>

### ~~P2-6. `total_elements` lệch so với số thực~~ ✅ ĐÃ SỬA

Đổi tên thành `estimated_total_elements` (đúng bản chất: density × diện tích), và
thêm `total_pages` **chính xác** để phân trang dựa vào. `next_page` giờ suy từ
`total_pages` nên không bao giờ trỏ tới trang rỗng.

<details><summary>Nội dung cũ</summary>


Claimed 678,961 · thực tế 678,926 — **lệch 35**.

[`nso/manufacturing/geometry.py`](src/backend/nso/manufacturing/geometry.py) tính `total` bằng `density × diện tích
hình tròn`, còn vòng lặp sinh trên **lưới vuông rồi cắt tròn**, hai cách đếm không
khớp. Hệ quả: `next_page` có thể báo còn trang trong khi trang đó rỗng.

Không nguy hiểm ngay, nhưng nếu xưởng dùng `total_elements` để kiểm tra đủ dữ liệu
thì sẽ báo thiếu.

</details>

### P2-7. Jitter chuyển từ độ sang mm bằng công thức tự đặt

[`nso/manufacturing/geometry.py`](src/backend/nso/manufacturing/geometry.py) — `_jitter_mm()`

```python
jitter_mm = pitch * math.sin(math.radians(recipe.spatial_jitter_deg))
```

`spatial_jitter_deg` mang đơn vị **độ**, nhưng jitter vị trí là **khoảng cách**.
Việc nhân `pitch × sin(góc)` là mình tự nghĩ ra cho có đơn vị đúng — không có cơ sở
nào nói jitter phải quy đổi như vậy. Có khả năng bản thân việc lưu jitter bằng độ đã
là sai đơn vị ngay từ recipe.

### ~~P2-8. "Blue-noise" chỉ có trong comment~~ ✅ ĐÃ SỬA

Giờ là **Poisson-disk thật**: không hai phần tử nào gần nhau hơn bán kính đĩa. Đo
lại: `min khoảng cách 0.0346 mm` vs `radius 0.0346 mm` → đạt.

Thuật toán: **dart-throwing với ưu tiên xác định**. Mỗi ô lưới sinh một ứng viên
kèm mức ưu tiên từ hash; ứng viên bị loại nếu có ứng viên ưu tiên cao hơn nằm trong
bán kính. Chỉ vùng lân cận 5×5 có thể xung đột (ô rộng `radius/√2`), nên **kiểm tra
cục bộ và vẫn truy cập được theo trang** — điều thuật toán Bridson tuần tự không làm được.

Hai lần tối ưu tốc độ:
- Bỏ SHA-256 mỗi ô → SplitMix64. Ở đây **không cần hash mật mã** (toạ độ vẫn giao
  cho xưởng), mà SHA-256 đắt gấp ~30 lần.
- Gom dải 5 hàng theo **cả trang** thay vì từng hàng.

Kết quả: 32.8s → **9.8s** cho 325k phần tử, truy cập ngẫu nhiên 0.1s.

<details><summary>Nội dung cũ</summary>

### P2-8. "Blue-noise" trong comment nhưng code là jittered grid

[`nso/manufacturing/geometry.py`](src/backend/nso/manufacturing/geometry.py) — `microstructure_map()` — docstring nói "blue-noise", code thực
tế là **lưới vuông + nhiễu đều**. Đây là hai thứ khác nhau về phổ không gian; blue-noise
thật cần Poisson-disk sampling hoặc void-and-cluster.

Nếu đặc tính blue-noise là quan trọng về mặt quang học (rất có thể, vì nó tránh
nhiễu xạ bậc cao) thì **code hiện tại không tạo ra thứ tài liệu mô tả**.
</details>

### P2-9. Số mẫu bề mặt 41 × 24 là chọn bừa

[`nso/config.py`](src/backend/nso/config.py) — `surface_*_samples`

984 điểm cho cả mặt kính = độ phân giải **0.5 mm theo bán kính, 15° theo chu vi**.
Quá thô cho gia công diamond turning thật (thường cần vài chục nghìn điểm). Chọn cho
payload nhẹ khi demo.

### ~~P2-10. Verification chỉ ghi verdict~~ ✅ ĐÃ SỬA

Giờ lưu cả `checks` (giá trị đo, ngưỡng, đạt/không) và `checked_at`.

<details><summary>Nội dung cũ</summary>


[`nso/manufacturing/verification.py`](src/backend/nso/manufacturing/verification.py) —
`entry.setdefault("verifications", []).append(verdict)`

Chỉ lưu chuỗi `"Pass"`. Mất giá trị đo, mất thời điểm, mất ai đo. Không truy vết
được, và không dùng được để cải thiện bù trừ gia công sau này.

</details>

### ~~P2-11. Guard chỉ quét key~~ ✅ ĐÃ SỬA

Thêm `FORBIDDEN_VALUE_FRAGMENTS` — guard giờ quét cả chuỗi văn bản, kể cả trong
list. `{"note": "spherical aberration 3.76D"}` bị chặn.

<details><summary>Nội dung cũ</summary>


[`nso/ip.py`](src/backend/nso/ip.py)

Docstring nói "no Design IP key or value is present" nhưng code chỉ kiểm tra tên key.
Một payload dạng `{"note": "SA strength is 3.76D"}` **lọt qua**.

Hiện tại không có chỗ nào làm vậy (test text-level bắt được), nhưng cái chốt chặn
không mạnh như docstring tuyên bố.

</details>

### P2-12. Danh sách từ cấm là chuỗi con, gây dương tính giả

[`nso/ip.py`](src/backend/nso/ip.py)

`"density"` nằm trong danh sách cấm → bất kỳ key nào chứa "density" đều bị chặn, kể
cả key lâm sàng chính đáng. Đây là lý do phải đặt tên vòng vo ở vài chỗ. Không sai,
nhưng sẽ gây khó chịu khi mở rộng.

---

## ⚪ P3 — Dọn dẹp

### P3-1. `BASE_CURVE_D` khai báo nhưng không dùng ở đâu

[`nso/config.py`](src/backend/nso/config.py) — `base_curve_d`. Trông như một tham số thiết kế có ý
nghĩa, thực tế là hằng số chết. Hoặc dùng nó trong `surface_map()`, hoặc xoá.

### ~~Streamlit console lộ recipe~~ ✅ ĐÃ SỬA

Chuyển `nso_mvp.py` → `internal/design_console.py`, **ra ngoài `src/backend`** —
tức ra ngoài deploy root, nên không thể vô tình đóng gói. Thêm chốt thứ hai: từ
chối chạy nếu thiếu `NSO_INTERNAL_CONSOLE=1`. Và gỡ `streamlit` khỏi
`requirements.txt` của bản deploy.

Dán nhãn "internal" không chặn được deploy; **dời khỏi build** thì có.

### P3-2. `robustness` trong `run_prediction` là bốn số cứng

[`nso_core.py:661`](src/backend/nso_core.py#L661)

```python
robustness = dynamic_robustness(85, 80, 75, 90)
```

Nghĩa là **mọi bệnh nhân đều có robustness giống hệt nhau** (86.0). V2 không dùng
giá trị này (nó dùng `dynamic_robustness_index`), nhưng **app Streamlit vẫn hiển thị
nó như một chỉ số cá nhân hoá** — gây hiểu nhầm.

### P3-3. `zone_count = 3` cứng ở mọi thiết kế

[`nso/config.py`](src/backend/nso/config.py) — `zone_count`. Không bệnh nhân nào nhận số vùng khác 3.
Nếu số vùng đáng lẽ phải cá nhân hoá thì đây là một chiều thiết kế bị bỏ quên.

### ~~P3-4. Candidate chỉ một trục~~ ✅ ĐÃ SỬA

Lưới candidate giờ **hai trục**: SA × density, 3×3 = 9 candidate mỗi mắt, 81 cặp
cho joint optimization (12 ms).

Nhưng mở lưới thôi chưa đủ — lần đầu vẫn ra 2 đáp án, vì **density chỉ có nhược
điểm**: fill factor tăng thì comfort/adaptation/robustness đều giảm mà control
không đổi, nên density thấp nhất luôn thắng. Cùng loại lỗi với SA trước đây.

Đã thêm `control_fill_factor_sensitivity`: **fill factor là tỉ lệ diện tích mang
tín hiệu defocus**, nên phủ nhiều hơn = kiểm soát nhiều hơn. Đây là tiền đề của cả
lớp sản phẩm này.

Giờ hai trục trade khác nhau thật:

| Tăng | control | acuity | comfort | robustness |
|---|---|---|---|---|
| **SA** (cường độ) | ↑ | **↓** | ↓ | ↓ |
| **Fill factor** (độ phủ) | ↑ | **—** | ↓ | ↓ |

Tức muốn thêm kiểm soát mà giữ thị lực thì mua bằng **độ phủ**, không phải cường độ.

<details><summary>Nội dung cũ</summary>

### P3-4. `CANDIDATE_OFFSETS = (-1.0, 0.0, 1.0)`

[`nso/config.py`](src/backend/nso/config.py) — `candidate_offsets`. Chỉ khám phá ±1 D quanh SA. Bước 1 D và
số lượng 3 candidate đều chọn bừa. Tài liệu thầy nói "Candidate Comparison Table"
nhưng không nói bao nhiêu candidate hay khám phá theo chiều nào.
</details>

### P3-5. Nhãn "Personalized Functional Profile A / B" là cố định

[`nso/clinical.py`](src/backend/nso/clinical.py). OD luôn là A, OS luôn là B — kể cả
khi hai mắt nhận thiết kế **giống hệt nhau**. Nhãn không mang thông tin.

### P3-6. `site = "SG"` mặc định

Xuất hiện trong job ID `NSO-26-SG-...`. Lấy từ ví dụ của thầy, chưa biết SG là gì
(Singapore? một mã xưởng?).

---

---

## 🔴 P1-13. Trọng số loss theo Primary Optimization Goal

[`nso/config.py`](src/backend/nso/config.py) — `goal_loss_weights`

Dropdown "Primary visual goal" giờ đổi **trọng số hàm loss** (ta coi trọng gì),
không đổi hệ số tổng hợp thiết kế (thiết kế được sinh ra thế nào).

Thứ tự ưu tiên thì phát biểu được — nhìn đêm cần acuity và độ bền theo đồng tử;
thoải mái với màn hình cần comfort và adaptation. Nhưng **độ lớn cụ thể là bịa**,
như mọi trọng số khác.

Hạn chế còn lại: chỉ có 3 candidate biến thiên trên **một trục duy nhất** (SA),
nên 9 mục tiêu chỉ cho ra tối đa 2 đáp án khác nhau. Muốn phân hoá mịn hơn phải
mở rộng không gian candidate sang density / fill factor — xem P3-4.

## 🔴 P1-14. Quan hệ SA ↔ control / acuity / robustness

[`nso/config.py`](src/backend/nso/config.py) — `control_sa_sensitivity`,
`target_mtf_sa_gain`, `robustness_entropy_penalty`

Ba quan hệ vừa được thêm vào vì thiếu chúng thì bảng candidate **vô nghĩa**
(xem mục "Lỗi đã sửa" bên dưới):

- `control_sa_sensitivity = 1.0` — tỉ lệ tuyến tính: SA cao hơn nominal 20% thì
  control cao hơn 20%. Đây là **phát biểu được**, không phải số vặn cho ra kết
  quả mong muốn. Nhưng chưa hiệu chỉnh.
- `target_mtf_sa_gain = -0.02` /D — tải quang học cao thì MTF giảm. Độ dốc bịa.
- `robustness_entropy_penalty = 0.25` — bề mặt phức tạp hơn thì kém dung sai
  lệch tâm hơn. Hệ số bịa.

## 🔴 P1-18. Hướng vergence — đã nối, hệ số = 0

[`nso/config.py`](src/backend/nso/config.py) — `sa_vergence_direction_gain`

**Eso đã được xử lý.** Trước đây `binocular_load` chỉ đọc phần exo tính từ 0, nên
mọi bệnh nhân lác trong đều chấm điểm như không có vấn đề gì. Giờ đo **độ lệch so
với chuẩn Morgan** (−3Δ exo ở gần), hai chiều:

| phoria | trước | sau |
|---|---|---|
| exo −8Δ | 41.0 | 41.0 |
| **chuẩn −3Δ** | 17.1 | **17.1** ← thấp nhất, đúng |
| ortho 0Δ | 17.1 | 31.4 |
| **eso +8Δ** | **17.1** ← sai | **69.5** |

Cũng đã dùng **tiêu chuẩn Sheard** cho dự trữ hợp thị: dự trữ *đối kháng* phải ≥ 2×
độ lệch — exo cần PFV, eso cần NFV. Đây là tiêu chuẩn y văn, không phải trọng số
bịa, nên dùng trực tiếp. Nhờ đó `nfv` hết chết.

**Nhưng phần thiết kế thì chưa.** Peripheral add làm giảm quy tụ do điều tiết →
giúp eso, hại exo. Hướng thì phát biểu được, **độ lớn thì không được bịa**, nên
`sa_vergence_direction_gain = 0.0`. Đường dẫn đã nối và có test: thầy điền một số
là xong.

## 🔴 P1-19. Wavefront và giác mạc — đã nối, hệ số = 0

[`nso/config.py`](src/backend/nso/config.py) — nhóm *Wavefront and corneal shape*

Mắt bệnh nhân **đã có sẵn quang sai**. Kính bỏ qua điều đó là kê đơn vào chỗ
trống: người đã có SA giác mạc dương nhiều thì không cần cộng thêm bấy nhiêu, và
người có ảnh võng mạc đã kém vì coma/trefoil thì chịu được ít tải quang học hơn.

Đã nối bốn đường:

| Hệ số | Ý nghĩa | Giá trị |
|---|---|---|
| `sa_corneal_compensation` | Trừ SA sẵn có của mắt khỏi SA kính | **0.0** |
| `sa_hoa_tolerance_gain` | Quang sai cao thì giảm tải thêm | **0.0** |
| `sa_corneal_asphericity_gain` | Giác mạc prolate đã tự tạo defocus ngoại vi | **0.0** |
| `dominance_asymmetry_gain` | Mắt chủ đạo nhận thiết kế nhẹ hơn | **0.0** |

Tất cả = 0 vì **cộng sai lượng SA còn tệ hơn không cộng**. Có test chứng minh chỉ
cần điền số là đường dẫn hoạt động.

**Một đường có hệ số khác 0:** quang sai sẵn có làm giảm `neural_adaptation`
(⚙️ −15 điểm ở mức tối đa). Hướng và độ lớn đại khái đều không gây tranh cãi.

Ngoài ra `residual_astigmatism = |trụ khúc xạ| − loạn giác mạc` là **định nghĩa**,
nên `corneal_astigmatism` hết chết.

## 🔴 P1-21. Cách hình học vùng co giãn theo mục tiêu

[`nso/config.py`](src/backend/nso/config.py) — nhóm *NSO three-zone architecture*

**Bảng tham chiếu là số thật của thầy:**

| Zone | Element size | Height | Fill factor |
|---|---|---|---|
| A | 22 µm | 2.2 µm | 38% |
| B | 28 µm | 1.5 µm | 35% |
| C | 32 × 16 µm | 1.0 µm | 28% |

Mục tiêu quang học từng tier cũng là số thật: Low `1-3-2D`, Medium `3-5-4D`,
High `5-8-6D`.

**Nhưng những thứ sau vẫn là nội suy của mình:**

- ⚙️ Ranh giới bán kính vùng A/B/C — thầy **không nêu**. Mình chia 0–4, 4–9, 9–16 mm.
- ⚙️ Hình học co giãn thế nào khi mục tiêu lệch khỏi tier tham chiếu
  (`zone_height_target_exponent` v.v.)
- ⚙️ Vector nhu cầu ánh xạ sang hình học ra sao (`realized`, `trim`,
  `contrast_relief`, trọng số ngoại vi)

Thầy cũng cảnh báo rõ: `3-5-4D` **không phải** ba giá trị sag cố định, phải đi qua
`Optical target → geometry transfer function → compensated manufacturing geometry`.
Có test khẳng định `height ≠ target_d` cho mọi vùng.

## 🔴 P1-22. Mô hình truyền quy trình

[`nso/config.py`](src/backend/nso/config.py) — nhóm *Process transfer*

Một dữ liệu duy nhất từ thầy: lớp phủ HC làm cấu trúc danh nghĩa **3.0 µm** chỉ
còn **~1.8 µm** — tức retention **0.6**. Đã cài và kiểm chứng đúng con số.

⚙️ Nhưng đó là **một quan sát đơn lẻ**, không phải đường cong đo qua dải quy trình.
`diameter_growth = 0.05` và `fill_factor_growth = 0.08` thì hoàn toàn bịa.

Điểm quan trọng về kiến trúc: thầy nhấn mạnh việc HC biến 3.0 thành 1.8 µm **thuộc
về Manufacturing Compensation, không phải thuật toán AI fitting**. Có test khẳng
định đổi hệ số bù trừ **không** làm đổi thiết kế.

## 🔴 P1-15. Khoảng "hợp lý" của 33 phép đo

[`nso/config.py`](src/backend/nso/config.py) — `plausible_ranges`

Dùng để quyết định giá trị nào bị coi là bất thường và trừ confidence. Đây là
**khoảng lâm sàng thông thường**, không phải phân bố huấn luyện của mô hình nào.
Khi có model thật phải thay bằng phân bố dữ liệu thật.

Hình phạt `plausibility_penalty_per_violation = 0.25` cũng là số bịa.

## 🔴 P1-16. Trọng số nhu cầu điều tiết

[`nso/config.py`](src/backend/nso/config.py) — `accommodative_demand_weight`

`demand = 100 / khoảng cách (cm)` là **định nghĩa**, không phải giả định. Nhưng
trọng số 0.20 của nó trong `accommodative_stress`, và khoảng quy chuẩn 2–5 D, thì
là bịa.

## 🔴 P1-20. Scalarization — tổng có trọng số không dùng được

[`nso/design/candidates.py`](src/backend/nso/design/candidates.py) — `score_candidates()`

Mở lưới candidate 2 trục xong vẫn chỉ ra 2 đáp án. Chuẩn hoá biên độ xong còn **1**.
Nguyên nhân gốc sâu hơn cả hai chẩn đoán trước:

**1. Năm mục tiêu ngoài control đều đo cùng một thứ.** Acuity, comfort,
adaptation, robustness, manufacturability đều tốt lên khi tải quang học giảm. Nên
candidate nhẹ nhất đạt **1.00 cả năm**, và thắng bất cứ khi nào trọng số control
< 0.5 — bất kể bảng trọng số nói gì.

**2. Tổng tuyến tính luôn chọn điểm cực trị.** Đây là tính chất toán học của việc
tối ưu hàm tuyến tính trên tập rời rạc, không phải đặc thù của bộ số này. Phương án
trung gian **không bao giờ** thắng được.

Đã đổi sang **augmented weighted Tchebycheff**: tối thiểu hoá *khoảng thiếu hụt tệ
nhất* thay vì tổng. Một thiết kế "khá ở mọi mặt" giờ có thể thắng thiết kế "hoàn hảo
5 mặt, tệ 1 mặt". Đây là cách chuẩn trong tối ưu đa mục tiêu, không phải mẹo vặn số.

Kết quả: **5/9 mục tiêu ra thiết kế khác nhau**, và toàn phương án **trung gian**
thắng chứ không phải hai góc:

| Mục tiêu | Chọn | SA | FF% | Logic |
|---|---|---|---|---|
| Myopia Management | F | 4.98 | 38.9 | Phủ cao nhất |
| Night Vision / Driving | C | 3.98 | 38.9 | **SA thấp, phủ cao** — giữ thị lực |
| Presbyopia | B | 3.98 | 34.7 | Nhẹ nhất |
| General Comfort | D | 4.98 | 30.5 | Phủ thấp nhất |
| Digital / Reading / Near / Sports | E | 4.98 | 34.7 | Cân bằng |

⚙️ `scalarization_augmentation = 0.05` là số tự đặt (nhỏ, chỉ phá hoà).

## 🔴 P1-17. Bảng trọng số theo mục tiêu đã sửa một lần

[`nso/config.py`](src/backend/nso/config.py) — `goal_loss_weights`

Bảng đầu tiên cho control xuống 0.10 ở hầu hết mục tiêu, khiến 8/9 mục tiêu chọn
thiết kế yếu nhất. Sai về lâm sàng: **trẻ đeo kính kiểm soát cận thì luôn cần
kiểm soát cận**, mục tiêu chỉ quyết định đánh đổi *xung quanh* nó.

Bảng mới giữ control ≥ 0.20 ở mọi mục tiêu → 4/9 thiết kế khác nhau.

⚠️ **Hạn chế còn lại:** độ phân hoá phụ thuộc vào *biên độ tương đối* giữa 6 hạng
mục loss, mà các biên độ đó đều chưa hiệu chỉnh. Ví dụ control trải 41→59 (18
điểm) còn acuity chỉ trải 56→60 (4 điểm), nên control áp đảo ngay cả khi trọng số
thấp hơn. Việc mục tiêu nào chọn thiết kế nào hiện là **hệ quả của các biên độ
này**, không phải của trọng số.

---

## Trạng thái sau đợt refactor

| Mục | Trạng thái |
|---|---|
| P2-3 key dùng chung | ✅ Đã sửa — key riêng từng vendor |
| P2-4 ngưỡng lặp | ✅ Đã sửa — dùng chung config |
| P2-5 phân trang O(n²) | ✅ Đã sửa — 93 s → 0.95 s |
| P2-6 `total_elements` lệch | ✅ Đã sửa — đổi tên + `total_pages` |
| P2-10 mất số đo verification | ✅ Đã sửa |
| P2-11 guard chỉ quét key | ✅ Đã sửa — quét cả value |
| P2-1 registry trong RAM | ✅ Đã sửa — SQLite/Postgres, kiểm bằng subprocess |
| P2-2 serial trùng | ✅ Đã sửa — lấy từ DB, nguyên tử |
| P0-3 roll-off gãy | 🟡 Đã trơn hoá (C²), hàm thật vẫn cần thầy |
| P0-5 verification nói dối | 🟡 Đã đổi tên + ghi rõ trong payload |
| P1-11 confidence chỉ đếm | ✅ Đã sửa — thêm plausibility, 33 khoảng |
| P3-4 candidate một trục | ✅ Đã sửa — lưới SA × density, 9 candidate |
| Streamlit lộ recipe | ✅ Đã sửa — ra khỏi deploy root + chốt env |
| Khoảng cách làm việc chết | ✅ Đã sửa — `demand = 100/cm` |
| Eso bị bỏ qua | ✅ Đã sửa — chuẩn Morgan + Sheard |
| P2-8 blue-noise giả | ✅ Đã sửa — Poisson-disk thật |
| P1-20 scalarization | ✅ Đã sửa — Tchebycheff, 5/9 thiết kế |
| Wavefront không vào thiết kế | ✅ Đã có hệ số từ thầy (25–50% / 10–20% / ±5–10%) |
| P0-2 hai kênh bị gộp | ✅ Đã tách — `BaseSurfaceProfile` / `NsoModulationProfile` |
| P0-3 roll-off | ✅ Thầy chọn (b)+(c) — cửa sổ W(r) có biên |
| P1-4 tần số CSF cứng | ✅ Đã sửa — schema đầy đủ, protocol đi theo phép đo |
| P1-5 gộp hai tầng | ✅ Đã tách — vector nhu cầu 0–1 độc lập sản phẩm |
| Kiến trúc 7 tầng | ✅ `nso/pipeline.py` — chạy được từng chặng |
| Tên "myopia control %" | ✅ Đổi thành NSO Control Score (relative) |
| P0-* và P1-* | 🔴 **Không đổi** — cần dữ liệu thật, không phải code |

### Lỗi input trơ (phát hiện 2026-08-21)

`primary_goal` được bác sĩ chọn nhưng **không ảnh hưởng gì** — 9 mục tiêu ra
cùng một thiết kế. Truy ngược ra lỗi sâu hơn: `control` và `acuity` không phụ
thuộc SA, nên candidate yếu nhất **áp đảo trên mọi trục** và không trọng số nào
lật được. Bảng candidate trông như một lựa chọn nhưng không phải.

Đã sửa cả hai (P1-13, P1-14). Thêm test chặn tái diễn:

- `test_clinical_input_changes_the_result` — mọi field lâm sàng phải đổi được kết quả
- `test_no_new_inert_clinical_inputs` — field mới không có ảnh hưởng thì fail
- `test_no_candidate_dominates_on_every_axis` — chặn bảng candidate giả
- `test_no_loss_term_is_constant_across_candidates` — chặn hạng mục loss trơ

**Còn 6 field vẫn trơ**, ghi rõ trong `KNOWN_INERT` của test:

| Field | Cần gì |
|---|---|
| `ocular_dominance` | Quy tắc lâm sàng: mắt chủ đạo lệch thiết kế OD/OS thế nào |
| `distance_phoria` | `binocular_load` hiện chỉ đọc near phoria |
| `near_working_distance` | Chưa suy accommodative demand từ khoảng cách |
| `computer_working_distance` | Như trên |
| `corneal_astigmatism` | Cần quy tắc quang học từ thầy |
| `corneal_eccentricity` | Cần quy tắc quang học từ thầy |

Và **9 field research chỉ tăng confidence** (`vep`, `erg`, `eye_tracking`,
`hoa_rms`, `corneal_sa`, `coma`, `trefoil`, `nfv`, `stereoacuity`) — nhập giá trị
bất thường cũng tăng confidence y hệt nhập giá trị bình thường, vì công thức chỉ
đếm *có đo hay không*. Xem P1-11.

Refactor **không sửa được** bất kỳ mục P0/P1 nào, và không nên kỳ vọng thế: đó là
những chỗ thiếu *dữ liệu*, không phải thiếu *cấu trúc*. Điều nó làm được là đưa
toàn bộ chúng vào `nso/config.py`, nên khi có số thật thì thay là xong, không phải
sửa code.

---

## Tóm tắt: cần hỏi ai cái gì

**Hỏi thầy (kỹ sư quang học):**
1. Spec cấu tạo tròng kính — chiết suất, đường kính, base curve, bán kính tham chiếu (P0-1)
2. SA dioptre chuyển sang sag thế nào (P0-2)
3. Số hạng bậc cao roll-off theo hàm gì (P0-3)
4. `"3-5-4D"` có phải ký hiệu thật không, `control_power` lấy từ đâu (P1-8)
5. Các hệ số ánh xạ phenotype → thiết kế (P1-5) — **đây là câu hỏi quan trọng nhất**

**Hỏi phòng khám:**
6. Máy đo CSF dùng tần số nào (P1-4)
7. Thang CSF nào đang dùng thực tế (P1-3)

**Hỏi xưởng gia công:**
8. Dung sai process capability (P0-4)
9. Muốn nhận định dạng file nào, độ phân giải bao nhiêu (P2-9)
10. Có cần blue-noise thật hay jittered grid là đủ (P2-8)

**Quyết định kỹ thuật nội bộ:**
11. Database thay cho registry trong RAM (P2-1, P2-2) — **chặn deploy**
12. Key riêng từng vendor (P2-3) — **chặn ý nghĩa bảo mật**

---

## Những gì **không** nằm trong danh sách này

Để công bằng, các phần sau **không** phải đoán:

- Ranh giới IP (tầng lâm sàng / design / manufacturing) — kiểm chứng bằng test, đã
  verify bằng fetch thật từ browser
- Tính deterministic của Design ID — cùng input ra cùng ID, test khoá
- Cơ chế phân trang, phân segment, xác thực key — logic đúng như thiết kế
- Toán sag hình cầu `R = (n−1)/F` và công thức sag — công thức quang hình chuẩn
  (chỉ *tham số đưa vào* mới là đoán)
- Trapezoid AUC, log-frequency axis — toán chuẩn
- 195 test đang pass — chúng kiểm tra code làm đúng thứ code định làm; chúng **không**
  kiểm tra được các hằng số trên có đúng ngoài đời hay không
