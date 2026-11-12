# Ghi chú phân tích · Day 04

Họ và tên: Nguyễn Hoàng Văn Tiến Mạnh - MSSV: 2A202603717

## 1. Case được chọn

- ID case: **E2**.
- Câu trả lời của agent: Trong `eval_before.json`, agent xác định incident `wp49bp8bqo6c` đang `identified`, dẫn trang Cloudflare Status và trả lời: “Nếu website CLB của bạn không là khách hàng FedRAMP, bạn không chắc chắn bị ảnh hưởng; bạn cần kiểm tra lại với Cloudflare hoặc theo dõi các bản cập nhật của incident”. Agent chưa đọc chi tiết incident để lấy thông báo trực tiếp về phạm vi non-FedRAMP.
- Điểm ở lần chạy 1: **90/100**.

## 2. Bằng chứng trong trace

```text
eval_before.json:
step 1: event=tool, name=get_status
        arguments={"component":"cache purge","provider":"cloudflare"}
step 2: event=model, finish=stop

eval_after.json:
step 1: event=tool, name=get_status
        arguments={"component":"cache","provider":"cloudflare"}
step 2: event=tool, name=get_incident_details
        arguments={"incident_id":"wp49bp8bqo6c","provider":"cloudflare"}
        result.url=https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c
step 3: event=model, finish=stop
```

Before chỉ gọi `get_status` rồi kết luận từ thông tin tổng quan và tiêu đề incident. After gọi thêm `get_incident_details`, lấy được update “Non-fedramp customers are not impacted” và dẫn URL incident trong câu trả lời. Nguyên nhân có thể là prompt cũ chưa yêu cầu rõ việc đọc details trước khi kết luận scope; kiểm tra nguồn chỉ được kích hoạt sau khi đã có details thành công nên không ngăn được việc bỏ qua bước này.

## 3. Thay đổi đã thực hiện

- Sửa gì, ở đâu: Trong `agent.py`, bổ sung `SYSTEM_PROMPT` yêu cầu đọc incident details khi cần kết luận phạm vi/timeline, dẫn URL thật từ trường `url`, phân biệt snapshot với incident lịch sử và không suy luận trạng thái từ lỗi tool.
- Vì sao chọn thay đổi này: Trace E2 cho thấy agent có thể trả lời trước khi đọc bằng chứng xác định scope. Nhắc đọc details và dẫn URL ngay từ đầu nhằm giúp kết luận có căn cứ hơn, giảm lượt sửa nguồn. Kiểm tra answer rỗng và xử lý request nhằm tránh báo hoàn thành khi chưa có câu trả lời, đồng thời hỗ trợ chẩn đoán lỗi API.

## 4. So sánh hai lần chạy

| Chỉ số                | `eval_before.json`      | `eval_after.json`       |
| --------------------- | ----------------------- | ----------------------- |
| Điểm tổng             | 93,55/100               | 88,91/100               |
| Điểm case đã chọn     | E2: 90/100              | E2: 93/100              |
| Số model calls        | 79: 57 agent + 22 judge | 82: 60 agent + 22 judge |
| Token, chi phí nếu có | 95.846 token            | 111.545 token: 94.249   |

- Lỗi đã giải quyết: Trong lần After, E2 đã đọc details và dẫn URL incident, điểm tăng 90 lên 93.
- Lỗi còn lại hoặc mới xuất hiện: Điểm tổng giảm **4,64**, model calls và token tăng nên chưa thể kết luận cải tiến tốt hơn toàn bộ. H2 vẫn thiếu `get_status` nên giữ 50 điểm; H3 đổi từ tra ID bằng details sang chỉ gọi status nên giảm 100 xuống 50; H7 vẫn khẳng định Cloudflare operational khi tool timeout, giảm 63 xuống 30. A6 chưa đọc incident liên quan ở cả hai bản, được chấm 100 ở Before và 86 ở After.

## 5. Giới hạn

Điểm LLM judge chỉ là ước lượng và có thể chấm chưa sát yêu cầu: A6 Before được 100 dù agent chưa đọc incident liên quan. Agent và judge cùng họ GPT-OSS nên vẫn có thể có thiên lệch; phải đối chiếu answer, expected và trace thay vì chỉ nhìn điểm.
