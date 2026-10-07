// PARENT-091：家長端常用接送人 api client（BACKEND-444 / 445 / 446）。
// 型別逐欄對照 apps/api/app/schemas/pickup.py 的 PickupPersonOut；photo_url 為短效簽名網址。
import { buildFormData } from '@/shared/http/upload'
import type { ISODateTime } from '@/shared/types/api'
import { parentHttp } from './http'

export interface PickupPerson {
  id: string
  student_id: string
  name: string
  /** 自由文字（例如「保母」），不是監護人的 relation enum */
  relation: string
  phone: string
  photo_url: string | null
  created_at: ISODateTime
}

export async function listPickupPersons(childId: string): Promise<PickupPerson[]> {
  const res = await parentHttp.get<PickupPerson[]>(`/parent/children/${childId}/pickup-persons`)
  return res.data
}

/** multipart：name、relation、phone，有照片才帶 photo；409 `pickup_person_limit_reached` 為已達上限 */
export async function createPickupPerson(
  childId: string,
  input: { name: string; relation: string; phone: string; photo?: File | null },
): Promise<PickupPerson> {
  const form = buildFormData({
    name: input.name,
    relation: input.relation,
    phone: input.phone,
    photo: input.photo,
  })
  const res = await parentHttp.post<PickupPerson>(`/parent/children/${childId}/pickup-persons`, form, {
    // parentHttp 預設 Content-Type 為 application/json，axios 遇到 FormData 會把它轉成 JSON 字串（檔案遺失）；
    // 這裡改成 multipart/form-data 讓 FormData 原樣送出。瀏覽器端 axios 送出前會清掉此標頭，由瀏覽器帶 boundary。
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return res.data
}

/** 軟刪除，後端回 204 */
export async function deletePickupPerson(id: string): Promise<void> {
  await parentHttp.delete(`/parent/pickup-persons/${id}`)
}
