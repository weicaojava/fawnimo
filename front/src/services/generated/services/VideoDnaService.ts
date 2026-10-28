/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_DnaCatalog_ } from '../models/ApiResponse_DnaCatalog_';
import type { ApiResponse_DnaCompareRead_ } from '../models/ApiResponse_DnaCompareRead_';
import type { ApiResponse_DnaDraft_ } from '../models/ApiResponse_DnaDraft_';
import type { ApiResponse_DnaPreflight_ } from '../models/ApiResponse_DnaPreflight_';
import type { ApiResponse_DnaPreparationRead_ } from '../models/ApiResponse_DnaPreparationRead_';
import type { ApiResponse_DnaSnapshot_ } from '../models/ApiResponse_DnaSnapshot_';
import type { DnaCompareRequest } from '../models/DnaCompareRequest';
import type { DnaCreate } from '../models/DnaCreate';
import type { DnaFreeze } from '../models/DnaFreeze';
import type { DnaImport } from '../models/DnaImport';
import type { DnaPatch } from '../models/DnaPatch';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class VideoDnaService {
    /**
     * Get Video Dna Catalog
     * List complete bounded actual source analyses, drafts and production versions.
     * @returns ApiResponse_DnaCatalog_ Successful Response
     * @throws ApiError
     */
    public static getVideoDnaCatalogApiV1VideoDnaProjectsProjectIdCatalogGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_DnaCatalog_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-dna/projects/{project_id}/catalog',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Video Dna Draft
     * Create an explicit editable hypothesis with fixed original source clocks.
     * @returns ApiResponse_DnaDraft_ Successful Response
     * @throws ApiError
     */
    public static createVideoDnaDraftApiV1VideoDnaProjectsProjectIdDraftsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: DnaCreate,
    }): CancelablePromise<ApiResponse_DnaDraft_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/video-dna/projects/{project_id}/drafts',
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
     * Get Video Dna Draft
     * Read every saved field, with source drift and missing evidence kept explicit.
     * @returns ApiResponse_DnaDraft_ Successful Response
     * @throws ApiError
     */
    public static getVideoDnaDraftApiV1VideoDnaProjectsProjectIdDraftsDraftIdGet({
        projectId,
        draftId,
    }: {
        projectId: string,
        draftId: string,
    }): CancelablePromise<ApiResponse_DnaDraft_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-dna/projects/{project_id}/drafts/{draft_id}',
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
     * Patch Video Dna Draft
     * Save a full document CAS revision without changing its fixed source.
     * @returns ApiResponse_DnaDraft_ Successful Response
     * @throws ApiError
     */
    public static patchVideoDnaDraftApiV1VideoDnaProjectsProjectIdDraftsDraftIdPatch({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: DnaPatch,
    }): CancelablePromise<ApiResponse_DnaDraft_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/video-dna/projects/{project_id}/drafts/{draft_id}',
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
     * Freeze Video Dna Draft
     * Freeze integer-only human declarations and real owned version/media evidence.
     * @returns ApiResponse_DnaSnapshot_ Successful Response
     * @throws ApiError
     */
    public static freezeVideoDnaDraftApiV1VideoDnaProjectsProjectIdDraftsDraftIdFreezePost({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: DnaFreeze,
    }): CancelablePromise<ApiResponse_DnaSnapshot_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/video-dna/projects/{project_id}/drafts/{draft_id}/freeze',
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
     * Get Video Dna Snapshot
     * Read immutable history without following a newer set of confirmed cuts.
     * @returns ApiResponse_DnaSnapshot_ Successful Response
     * @throws ApiError
     */
    public static getVideoDnaSnapshotApiV1VideoDnaProjectsProjectIdSnapshotsSnapshotIdGet({
        projectId,
        snapshotId,
    }: {
        projectId: string,
        snapshotId: string,
    }): CancelablePromise<ApiResponse_DnaSnapshot_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-dna/projects/{project_id}/snapshots/{snapshot_id}',
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
     * Preflight Video Dna Snapshot
     * Separate byte availability from current-source differences and content quality.
     * @returns ApiResponse_DnaPreflight_ Successful Response
     * @throws ApiError
     */
    public static preflightVideoDnaSnapshotApiV1VideoDnaProjectsProjectIdSnapshotsSnapshotIdPreflightGet({
        projectId,
        snapshotId,
    }: {
        projectId: string,
        snapshotId: string,
    }): CancelablePromise<ApiResponse_DnaPreflight_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-dna/projects/{project_id}/snapshots/{snapshot_id}/preflight',
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
     * Export Video Dna Snapshot
     * Download exactly the portable schema, with no private path or execution credential.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static exportVideoDnaSnapshotApiV1VideoDnaProjectsProjectIdSnapshotsSnapshotIdExportGet({
        projectId,
        snapshotId,
    }: {
        projectId: string,
        snapshotId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-dna/projects/{project_id}/snapshots/{snapshot_id}/export',
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
     * Import Video Dna Draft
     * Import inert v1 content as a new draft; no snapshot or model task is imported.
     * @returns ApiResponse_DnaDraft_ Successful Response
     * @throws ApiError
     */
    public static importVideoDnaDraftApiV1VideoDnaProjectsProjectIdImportsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: DnaImport,
    }): CancelablePromise<ApiResponse_DnaDraft_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/video-dna/projects/{project_id}/imports',
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
     * Prepare Video Dna Snapshot
     * Return seven hypothetical layers from one immutable snapshot, with execution always disabled.
     * @returns ApiResponse_DnaPreparationRead_ Successful Response
     * @throws ApiError
     */
    public static prepareVideoDnaSnapshot({
        projectId,
        snapshotId,
    }: {
        projectId: string,
        snapshotId: string,
    }): CancelablePromise<ApiResponse_DnaPreparationRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-dna/projects/{project_id}/snapshots/{snapshot_id}/preparation',
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
     * Compare Video Dna Snapshots
     * Compare old and new dependency users without modifying either saved version.
     * @returns ApiResponse_DnaCompareRead_ Successful Response
     * @throws ApiError
     */
    public static compareVideoDnaSnapshots({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: DnaCompareRequest,
    }): CancelablePromise<ApiResponse_DnaCompareRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/video-dna/projects/{project_id}/compare',
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
