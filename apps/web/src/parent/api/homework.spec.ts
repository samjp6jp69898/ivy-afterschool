import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { getChildHomework } from './homework'
import { parentHttp } from './http'

const HOMEWORK = {
  student_id: 's1',
  date: '2026-10-05',
  overall_status: 'in_progress',
  ready_eta: '17:30',
  items: [{ title: '數學習作 p.12', subject_name: '數學', status: 'doing' }],
  note: null,
  updated_at: '2026-10-05T08:30:00Z',
}

describe('homeworkApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('homeworkApi gets child homework', async () => {
    mock.onGet('/parent/children/s1/homework').reply(200, HOMEWORK)

    const homework = await getChildHomework('s1')

    expect(homework).toEqual(HOMEWORK)
    expect(mock.history.get[0]!.url).toBe('/parent/children/s1/homework')
    expect(mock.history.get[0]!.params).toBeUndefined()
  })

  it('homeworkApi passes date param', async () => {
    mock.onGet('/parent/children/s1/homework').reply(404, {
      error: { code: 'student_not_found', message: '找不到學生', details: null },
    })

    let failure: unknown = null
    await getChildHomework('s1', '2026-10-01').catch((e: unknown) => {
      failure = e
    })

    expect(mock.history.get[0]!.params).toEqual({ date: '2026-10-01' })
    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).code).toBe('student_not_found')
  })
})
