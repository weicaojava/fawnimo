/** Exercise the shipped workflow logic with the already locked TypeScript compiler; no extra test framework or supplier is involved. */
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const Module = require('node:module')
const ts = require('typescript')
const base = path.resolve(__dirname, '../src/pages/aiStudio/project/ProjectWorkbench')

/** Execute the actual TypeScript module, replacing only its generated-client boundary for unit-level transport cases. */
function loadSource(name, mocks = {}) {
  const filename = path.join(base, name)
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
  }).outputText
  const loaded = new Module(filename, module)
  loaded.filename = filename
  loaded.paths = Module._nodeModulePaths(path.dirname(filename))
  const originalRequire = loaded.require.bind(loaded)
  loaded.require = request => Object.hasOwn(mocks, request) ? mocks[request] : originalRequire(request)
  loaded._compile(compiled, filename)
  return loaded.exports
}

const flow = loadSource('productionFlow.ts')

/** Construct the public pagination contract, including the empty-list page used by the real backend. */
function pageOf(items, page, total) {
  return { data: { items, pagination: { page, page_size: 100, total, max_page: Math.max(1, Math.ceil(total / 100)) } } }
}

/** Minimal authored shot facts let these cases separate information confirmation from task state. */
function shot(id, status = 'ready') { return { id, chapter_id: 'chapter', status } }

/** A prompt or image task is deliberately not represented as an active video task. */
function runtime(id, video = false) {
  return { shot_id: id, has_active_tasks: true, has_active_video_tasks: video,
    has_active_prompt_tasks: !video, has_active_frame_tasks: false, active_task_count: 1 }
}

/** A successful summary includes an explicit idle row for every shot, even when it has no tasks. */
function idle(id) { return { shot_id: id, has_active_tasks: false, has_active_video_tasks: false,
  has_active_prompt_tasks: false, has_active_frame_tasks: false, active_task_count: 0 } }

/** Load the real pagination/orchestration code with an explicitly authored generated-client transport. */
function readers(generated) {
  return loadSource('projectFlowStats.ts', { '../../../../services/generated': generated, './productionFlow': flow })
}

test('the 101st shot is collected and remains the next unconfirmed shot', async () => {
  const all = Array.from({ length: 101 }, (_, index) => shot(`shot-${index}`, index === 100 ? 'pending' : 'ready'))
  const calls = []
  const rows = await flow.collectWorkflowPages(async (page, pageSize) => {
    calls.push([page, pageSize])
    return pageOf(all.slice((page - 1) * 100, page * 100), page, all.length)
  })
  assert.deepEqual(calls, [[1, 100], [2, 100]])
  assert.deepEqual(flow.countShotFlow(rows, rows.map(row => idle(row.id))), { totalShots: 101, pendingConfirmShots: 1, readyShots: 100, generatingShots: 0, firstPendingShotId: 'shot-100' })
})

test('an exact full last page terminates without a speculative extra request', async () => {
  let calls = 0
  const rows = await flow.collectWorkflowPages(async page => {
    calls++
    return pageOf(Array.from({ length: 100 }, (_, index) => ({ id: `${page}-${index}` })), page, 200)
  })
  assert.equal(rows.length, 200)
  assert.equal(calls, 2)
})

test('only a successful declared empty page is accepted as an empty list', async () => {
  assert.deepEqual(await flow.collectWorkflowPages(async () => pageOf([], 1, 0)), [])
  await assert.rejects(flow.collectWorkflowPages(async () => ({ data: null })), /读取不完整/)
})

test('a changed total between pages rejects the entire partial observation', async () => {
  await assert.rejects(flow.collectWorkflowPages(async page => pageOf(
    Array.from({ length: page === 1 ? 100 : 2 }, (_, i) => ({ id: `${page}-${i}` })), page, page === 1 ? 101 : 102)), /数量已变化/)
})

test('duplicate IDs across page boundaries are not silently counted or dropped', async () => {
  await assert.rejects(flow.collectWorkflowPages(async page => pageOf(page === 1
    ? Array.from({ length: 100 }, (_, i) => ({ id: `item-${i}` })) : [{ id: 'item-0' }], page, 101)), /发生重复/)
})

test('a missing final page is a visible read failure', async () => {
  await assert.rejects(flow.collectWorkflowPages(async page => pageOf(page === 1
    ? Array.from({ length: 100 }, (_, i) => ({ id: `item-${i}` })) : [], page, 101)), /分页不完整/)
})

test('wrong page identity and excessive totals cannot start an unbounded read', async () => {
  await assert.rejects(flow.collectWorkflowPages(async () => pageOf([], 2, 0)), /数量已变化/)
  let calls = 0
  await assert.rejects(flow.collectWorkflowPages(async () => { calls++; return pageOf([], 1, 10001) }), /超出读取范围/)
  assert.equal(calls, 1)
})

test('an upstream read rejection is not translated to zero workflow progress', async () => {
  const failure = new Error('authored network failure')
  await assert.rejects(flow.collectWorkflowPages(async () => { throw failure }), error => error === failure)
})

test('leaving a view stops the old collection before it can request another page', async () => {
  let current = true, calls = 0
  await assert.rejects(flow.collectWorkflowPages(async page => {
    calls++; current = false
    return pageOf(Array.from({ length: 100 }, (_, index) => ({ id: `item-${index}` })), page, 101)
  }, () => current), /读取已停止/)
  assert.equal(calls, 1)
})

test('confirmation counts remain independent of running video and prompt tasks', () => {
  assert.deepEqual(flow.countShotFlow([shot('a'), shot('b', 'pending')], [runtime('a', true), runtime('b')]),
    { totalShots: 2, pendingConfirmShots: 1, readyShots: 1, generatingShots: 1, firstPendingShotId: 'b' })
})

test('foreign or duplicated runtime rows fail rather than modifying chapter facts', () => {
  assert.throws(() => flow.countShotFlow([shot('a')], [runtime('foreign')]), /范围已变化/)
  assert.throws(() => flow.countShotFlow([shot('a')], [runtime('a'), runtime('a')]), /范围已变化/)
})

test('a missing runtime row is unknown rather than evidence of no unfinished video task', () => {
  assert.throws(() => flow.countShotFlow([shot('a')], []), /读取不完整/)
  assert.deepEqual(flow.countShotFlow([], []), { totalShots: 0, pendingConfirmShots: 0, readyShots: 0, generatingShots: 0, firstPendingShotId: undefined })
})

test('duplicate shot input cannot inflate workflow counts', () => {
  assert.throws(() => flow.countShotFlow([shot('a'), shot('a')], [idle('a')]), /分镜范围发生重复/)
})

test('legacy done or shooting chapter status never bypasses unknown shot confirmation', () => {
  for (const status of ['done', 'shooting']) {
    const chapter = { rawText: 'original', storyboardCount: 1, status }
    assert.equal(flow.chapterFlowStep(chapter).key, 'prepare_shots')
    assert.equal(flow.chapterFlowStep(chapter, { totalShots: 1, pendingConfirmShots: 1, readyShots: 0, generatingShots: 1 }).key, 'prepare_shots')
  }
})

test('all confirmed shots lead only to generation preparation, without claiming video readiness', () => {
  const step = flow.chapterFlowStep({ rawText: '', storyboardCount: 1 }, { totalShots: 1, pendingConfirmShots: 0, readyShots: 1, generatingShots: 1 })
  assert.equal(step.key, 'shoot')
  assert.equal(step.primaryAction, '进入生成工作室')
  assert.match(step.hint, /检查视频生成条件/)
})

test('an actual empty shot observation overrides a stale chapter shot count', () => {
  const zero = { totalShots: 0, pendingConfirmShots: 0, readyShots: 0, generatingShots: 0 }
  assert.equal(flow.chapterFlowStep({ rawText: 'original', storyboardCount: 99 }, zero).key, 'extract_shots')
  assert.equal(flow.chapterFlowStep({ rawText: '', storyboardCount: 0 }, zero).key, 'edit_raw')
})

test('totals reuse the same observed chapter rows and retain independent overlapping counts', () => {
  assert.deepEqual(flow.sumChapterFlow([{ totalShots: 1, pendingConfirmShots: 0, readyShots: 1, generatingShots: 1 },
    { totalShots: 2, pendingConfirmShots: 1, readyShots: 1, generatingShots: 0 }]),
  { totalShots: 3, pendingConfirmShots: 1, readyShots: 2, generatingShots: 1 })
})

test('the generated chapter client keeps project scope on every page including chapter101', async () => {
  const calls = []
  const api = readers({ StudioChaptersService: { listChaptersApiV1StudioChaptersGet: async args => {
    calls.push(args)
    return pageOf(Array.from({ length: args.page === 1 ? 100 : 1 }, (_, i) => ({ id: `chapter-${(args.page - 1) * 100 + i}`, project_id: 'project' })), args.page, 101)
  } } })
  const rows = await api.loadCompleteProjectChapters('project')
  assert.equal(rows.length, 101)
  assert.deepEqual(calls.map(call => [call.projectId, call.page, call.pageSize, call.order, call.isDesc]),
    [['project', 1, 100, 'index', false], ['project', 2, 100, 'index', false]])
})

test('mixed project or chapter responses are rejected by the actual generated-client readers', async () => {
  const api = readers({ StudioChaptersService: { listChaptersApiV1StudioChaptersGet: async () => pageOf([{ id: 'chapter', project_id: 'other' }], 1, 1) },
    StudioShotsService: { listShotsApiV1StudioShotsGet: async () => pageOf([{ id: 'shot', chapter_id: 'other' }], 1, 1) } })
  await assert.rejects(api.loadCompleteProjectChapters('project'), /不属于当前工程/)
  await assert.rejects(api.loadCompleteChapterShots('chapter'), /不属于当前章节/)
})

test('chapter observations bound concurrent generated-client requests and retain all chapter results', async () => {
  let active = 0, peak = 0, calls = 0
  /** Delay both authored read endpoints so their actual concurrency is measurable. */
  async function read(result) {
    calls++; active++; peak = Math.max(peak, active)
    await new Promise(resolve => setTimeout(resolve, 5))
    active--
    return result
  }
  const api = readers({ StudioShotsService: {
    listShotsApiV1StudioShotsGet: args => read(pageOf([{ id: args.chapterId + '-shot', chapter_id: args.chapterId, status: 'pending' }], args.page, 1)),
    listShotRuntimeSummaryApiV1StudioShotsRuntimeSummaryGet: args => read({ data: [idle(args.chapterId + '-shot')] }),
  } })
  const rows = await api.loadChapterFlowStats(Array.from({ length: 9 }, (_, i) => ({ id: `chapter-${i}` })))
  assert.equal(rows.length, 9)
  assert.equal(rows[8].firstPendingShotId, 'chapter-8-shot')
  assert.equal(calls, 18)
  assert.ok(peak <= 8, `at most four chapter observations, each with two read endpoints; got ${peak}`)
})
