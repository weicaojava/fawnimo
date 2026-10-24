/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_list_ScriptRead__ } from '../models/ApiResponse_list_ScriptRead__';
import type { ApiResponse_ScriptCatalog_ } from '../models/ApiResponse_ScriptCatalog_';
import type { ApiResponse_ScriptPreflight_ } from '../models/ApiResponse_ScriptPreflight_';
import type { ApiResponse_ScriptRead_ } from '../models/ApiResponse_ScriptRead_';
import type { ApiResponse_ScriptSnapshot_ } from '../models/ApiResponse_ScriptSnapshot_';
import type { ScriptCreate } from '../models/ScriptCreate';
import type { ScriptPatch } from '../models/ScriptPatch';
import type { ScriptPublish } from '../models/ScriptPublish';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ScriptPreparationService {
    /**
     * Get Script Catalog
     * List complete actual project character and scene options with explicit bounds.
     * @returns ApiResponse_ScriptCatalog_ Successful Response
     * @throws ApiError
     */
    public static getScriptCatalogApiV1ScriptPreparationProjectsProjectIdCatalogGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_ScriptCatalog_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/script-preparation/projects/{project_id}/catalog',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Script Drafts
     * Read a bounded project draft list with realtime fact usability.
     * @returns ApiResponse_list_ScriptRead__ Successful Response
     * @throws ApiError
     */
    public static listScriptDraftsApiV1ScriptPreparationProjectsProjectIdDraftsGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_list_ScriptRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/script-preparation/projects/{project_id}/drafts',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Script Draft
     * Register one chapter's manual script without overwriting original novel text.
     * @returns ApiResponse_ScriptRead_ Successful Response
     * @throws ApiError
     */
    public static createScriptDraftApiV1ScriptPreparationProjectsProjectIdDraftsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: ScriptCreate,
    }): CancelablePromise<ApiResponse_ScriptRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/script-preparation/projects/{project_id}/drafts',
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
     * Get Script Draft
     * Read original required denominator and current mapping deficiencies.
     * @returns ApiResponse_ScriptRead_ Successful Response
     * @throws ApiError
     */
    public static getScriptDraftApiV1ScriptPreparationProjectsProjectIdDraftsDraftIdGet({
        projectId,
        draftId,
    }: {
        projectId: string,
        draftId: string,
    }): CancelablePromise<ApiResponse_ScriptRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/script-preparation/projects/{project_id}/drafts/{draft_id}',
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
     * Patch Script Draft
     * Save a new draft revision with CAS conflict protection.
     * @returns ApiResponse_ScriptRead_ Successful Response
     * @throws ApiError
     */
    public static patchScriptDraftApiV1ScriptPreparationProjectsProjectIdDraftsDraftIdPatch({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: ScriptPatch,
    }): CancelablePromise<ApiResponse_ScriptRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/script-preparation/projects/{project_id}/drafts/{draft_id}',
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
     * Publish Script Draft
     * Publish immutable manual evidence, not M4 or provider readiness.
     * @returns ApiResponse_ScriptSnapshot_ Successful Response
     * @throws ApiError
     */
    public static publishScriptDraftApiV1ScriptPreparationProjectsProjectIdDraftsDraftIdPublishPost({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: ScriptPublish,
    }): CancelablePromise<ApiResponse_ScriptSnapshot_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/script-preparation/projects/{project_id}/drafts/{draft_id}/publish',
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
     * Get Script Snapshot
     * Export historical evidence without re-certifying current facts.
     * @returns ApiResponse_ScriptSnapshot_ Successful Response
     * @throws ApiError
     */
    public static getScriptSnapshotApiV1ScriptPreparationProjectsProjectIdSnapshotsSnapshotIdGet({
        projectId,
        snapshotId,
    }: {
        projectId: string,
        snapshotId: string,
    }): CancelablePromise<ApiResponse_ScriptSnapshot_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/script-preparation/projects/{project_id}/snapshots/{snapshot_id}',
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
     * Preflight Script Snapshot
     * Reject revisions or actual facts that drifted after manual publication.
     * @returns ApiResponse_ScriptPreflight_ Successful Response
     * @throws ApiError
     */
    public static preflightScriptSnapshotApiV1ScriptPreparationProjectsProjectIdSnapshotsSnapshotIdPreflightGet({
        projectId,
        snapshotId,
    }: {
        projectId: string,
        snapshotId: string,
    }): CancelablePromise<ApiResponse_ScriptPreflight_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/script-preparation/projects/{project_id}/snapshots/{snapshot_id}/preflight',
            path: {
                'project_id': projectId,
                'snapshot_id': snapshotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
