/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_ExportCatalog_ } from '../models/ApiResponse_ExportCatalog_';
import type { ApiResponse_ExportRead_ } from '../models/ApiResponse_ExportRead_';
import type { ApiResponse_list_TransitionDraftRead__ } from '../models/ApiResponse_list_TransitionDraftRead__';
import type { ApiResponse_TransitionDraftRead_ } from '../models/ApiResponse_TransitionDraftRead_';
import type { ApiResponse_TransitionPublicationRead_ } from '../models/ApiResponse_TransitionPublicationRead_';
import type { ExportCancel } from '../models/ExportCancel';
import type { ExportRequest } from '../models/ExportRequest';
import type { TransitionCreate } from '../models/TransitionCreate';
import type { TransitionFreeze } from '../models/TransitionFreeze';
import type { TransitionPatch } from '../models/TransitionPatch';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class EditingV2Service {
    /**
     * List Transition Drafts
     * List actual variants in a chapter; optional base filtering never creates data.
     * @returns ApiResponse_list_TransitionDraftRead__ Successful Response
     * @throws ApiError
     */
    public static listTransitionDrafts({
        projectId,
        chapterId,
        basePublicationId,
    }: {
        projectId: string,
        chapterId: string,
        basePublicationId?: (string | null),
    }): CancelablePromise<ApiResponse_list_TransitionDraftRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing-v2/projects/{project_id}/drafts',
            path: {
                'project_id': projectId,
            },
            query: {
                'chapter_id': chapterId,
                'base_publication_id': basePublicationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Transition Draft
     * Create an independent variant tied to the selected actual v1 publication.
     * @returns ApiResponse_TransitionDraftRead_ Successful Response
     * @throws ApiError
     */
    public static createTransitionDraft({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: TransitionCreate,
    }): CancelablePromise<ApiResponse_TransitionDraftRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/editing-v2/projects/{project_id}/drafts',
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
     * Patch Transition Draft
     * Save all boundary choices using the expected saved revision.
     * @returns ApiResponse_TransitionDraftRead_ Successful Response
     * @throws ApiError
     */
    public static patchTransitionDraft({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: TransitionPatch,
    }): CancelablePromise<ApiResponse_TransitionDraftRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/editing-v2/projects/{project_id}/drafts/{draft_id}',
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
     * Freeze Transition Draft
     * Lock verified original sources and explicit overlap into a new v2 publication.
     * @returns ApiResponse_TransitionPublicationRead_ Successful Response
     * @throws ApiError
     */
    public static freezeTransitionDraft({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: TransitionFreeze,
    }): CancelablePromise<ApiResponse_TransitionPublicationRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/editing-v2/projects/{project_id}/drafts/{draft_id}/freeze',
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
     * Get Transition Publication
     * Inspect immutable v2 history without replacing v1 or current adoptions.
     * @returns ApiResponse_TransitionPublicationRead_ Successful Response
     * @throws ApiError
     */
    public static getTransitionPublication({
        projectId,
        publicationId,
    }: {
        projectId: string,
        publicationId: string,
    }): CancelablePromise<ApiResponse_TransitionPublicationRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing-v2/projects/{project_id}/publications/{publication_id}',
            path: {
                'project_id': projectId,
                'publication_id': publicationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Transition Exports
     * Read only the independent v2 render queue and actual local tool availability.
     * @returns ApiResponse_ExportCatalog_ Successful Response
     * @throws ApiError
     */
    public static listTransitionExports({
        projectId,
        chapterId,
    }: {
        projectId: string,
        chapterId: string,
    }): CancelablePromise<ApiResponse_ExportCatalog_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing-v2/projects/{project_id}/exports',
            path: {
                'project_id': projectId,
            },
            query: {
                'chapter_id': chapterId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Transition Export
     * Queue a real linear-crossfade video/audio render with a durable replay key.
     * @returns ApiResponse_ExportRead_ Successful Response
     * @throws ApiError
     */
    public static createTransitionExport({
        projectId,
        publicationId,
        requestBody,
    }: {
        projectId: string,
        publicationId: string,
        requestBody: ExportRequest,
    }): CancelablePromise<ApiResponse_ExportRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/editing-v2/projects/{project_id}/publications/{publication_id}/exports',
            path: {
                'project_id': projectId,
                'publication_id': publicationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Transition Export
     * Read a durable v2 outcome and verify every downloadable output.
     * @returns ApiResponse_ExportRead_ Successful Response
     * @throws ApiError
     */
    public static getTransitionExport({
        projectId,
        exportId,
    }: {
        projectId: string,
        exportId: string,
    }): CancelablePromise<ApiResponse_ExportRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing-v2/projects/{project_id}/exports/{export_id}',
            path: {
                'project_id': projectId,
                'export_id': exportId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cancel Transition Export
     * Cancel only this owned local v2 job; no model budget is touched.
     * @returns ApiResponse_ExportRead_ Successful Response
     * @throws ApiError
     */
    public static cancelTransitionExport({
        projectId,
        exportId,
        requestBody,
    }: {
        projectId: string,
        exportId: string,
        requestBody: ExportCancel,
    }): CancelablePromise<ApiResponse_ExportRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/editing-v2/projects/{project_id}/exports/{export_id}/cancel',
            path: {
                'project_id': projectId,
                'export_id': exportId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Transition Export Content
     * Stream fixed-name outputs only after source ownership and full byte hashes are checked.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static readTransitionExportContent({
        projectId,
        exportId,
        kind,
    }: {
        projectId: string,
        exportId: string,
        kind: 'video' | 'subtitles_srt' | 'subtitles_vtt' | 'package',
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing-v2/projects/{project_id}/exports/{export_id}/content/{kind}',
            path: {
                'project_id': projectId,
                'export_id': exportId,
                'kind': kind,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
