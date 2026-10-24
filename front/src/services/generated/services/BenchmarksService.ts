/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_BenchmarkRead_ } from '../models/ApiResponse_BenchmarkRead_';
import type { ApiResponse_BenchmarkSnapshot_ } from '../models/ApiResponse_BenchmarkSnapshot_';
import type { ApiResponse_list_BenchmarkRead__ } from '../models/ApiResponse_list_BenchmarkRead__';
import type { DraftCreate } from '../models/DraftCreate';
import type { DraftPatch } from '../models/DraftPatch';
import type { FreezeWrite } from '../models/FreezeWrite';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class BenchmarksService {
    /**
     * List Benchmark Drafts
     * List bounded owned drafts after checking their original sources.
     * @returns ApiResponse_list_BenchmarkRead__ Successful Response
     * @throws ApiError
     */
    public static listBenchmarkDraftsApiV1BenchmarksProjectsProjectIdDraftsGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_list_BenchmarkRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/benchmarks/projects/{project_id}/drafts',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Benchmark Draft
     * Create an incomplete draft without implying any external qualification.
     * @returns ApiResponse_BenchmarkRead_ Successful Response
     * @throws ApiError
     */
    public static createBenchmarkDraftApiV1BenchmarksProjectsProjectIdDraftsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: DraftCreate,
    }): CancelablePromise<ApiResponse_BenchmarkRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/benchmarks/projects/{project_id}/drafts',
            path: {
                'project_id': projectId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Benchmark Draft
     * Return one owned draft and its software checklist issues.
     * @returns ApiResponse_BenchmarkRead_ Successful Response
     * @throws ApiError
     */
    public static getBenchmarkDraftApiV1BenchmarksProjectsProjectIdDraftsDraftIdGet({
        projectId,
        draftId,
    }: {
        projectId: string,
        draftId: string,
    }): CancelablePromise<ApiResponse_BenchmarkRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/benchmarks/projects/{project_id}/drafts/{draft_id}',
            path: {
                'project_id': projectId,
                'draft_id': draftId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Patch Benchmark Draft
     * CAS a complete replacement document at its expected draft revision.
     * @returns ApiResponse_BenchmarkRead_ Successful Response
     * @throws ApiError
     */
    public static patchBenchmarkDraftApiV1BenchmarksProjectsProjectIdDraftsDraftIdPatch({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: DraftPatch,
    }): CancelablePromise<ApiResponse_BenchmarkRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/benchmarks/projects/{project_id}/drafts/{draft_id}',
            path: {
                'project_id': projectId,
                'draft_id': draftId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Freeze Benchmark Draft
     * Freeze an immutable human-declared preparation snapshot, not Q0 PASSED.
     * @returns ApiResponse_BenchmarkSnapshot_ Successful Response
     * @throws ApiError
     */
    public static freezeBenchmarkDraftApiV1BenchmarksProjectsProjectIdDraftsDraftIdFreezePost({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: FreezeWrite,
    }): CancelablePromise<ApiResponse_BenchmarkSnapshot_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/benchmarks/projects/{project_id}/drafts/{draft_id}/freeze',
            path: {
                'project_id': projectId,
                'draft_id': draftId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Benchmark Snapshot
     * Export immutable JSON without runtime paths, credentials or execution hooks.
     * @returns ApiResponse_BenchmarkSnapshot_ Successful Response
     * @throws ApiError
     */
    public static getBenchmarkSnapshotApiV1BenchmarksProjectsProjectIdSnapshotsSnapshotIdGet({
        projectId,
        snapshotId,
    }: {
        projectId: string,
        snapshotId: string,
    }): CancelablePromise<ApiResponse_BenchmarkSnapshot_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/benchmarks/projects/{project_id}/snapshots/{snapshot_id}',
            path: {
                'project_id': projectId,
                'snapshot_id': snapshotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Import Benchmark Draft
     * Revalidate document against a local analysis; never import snapshot qualification.
     * @returns ApiResponse_BenchmarkRead_ Successful Response
     * @throws ApiError
     */
    public static importBenchmarkDraftApiV1BenchmarksProjectsProjectIdImportDraftPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: DraftCreate,
    }): CancelablePromise<ApiResponse_BenchmarkRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/benchmarks/projects/{project_id}/import-draft',
            path: {
                'project_id': projectId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
