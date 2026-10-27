/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_EditCatalog_ } from '../models/ApiResponse_EditCatalog_';
import type { ApiResponse_EditDraftRead_ } from '../models/ApiResponse_EditDraftRead_';
import type { ApiResponse_EditPreflightRead_ } from '../models/ApiResponse_EditPreflightRead_';
import type { ApiResponse_EditPublicationRead_ } from '../models/ApiResponse_EditPublicationRead_';
import type { ApiResponse_list_EditDraftRead__ } from '../models/ApiResponse_list_EditDraftRead__';
import type { ApiResponse_SelectionHistoryRead_ } from '../models/ApiResponse_SelectionHistoryRead_';
import type { ApiResponse_SelectionRead_ } from '../models/ApiResponse_SelectionRead_';
import type { EditDraftCreate } from '../models/EditDraftCreate';
import type { EditDraftPatch } from '../models/EditDraftPatch';
import type { EditFreezeWrite } from '../models/EditFreezeWrite';
import type { SelectionWrite } from '../models/SelectionWrite';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class EditingService {
    /**
     * Get Edit Catalog
     * List bounded actual plan, Take and owned MP4 choices.
     * @returns ApiResponse_EditCatalog_ Successful Response
     * @throws ApiError
     */
    public static getEditCatalogApiV1EditingProjectsProjectIdCatalogGet({
        projectId,
        chapterId,
        planId,
    }: {
        projectId: string,
        chapterId: string,
        planId?: (string | null),
    }): CancelablePromise<ApiResponse_EditCatalog_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing/projects/{project_id}/catalog',
            path: {
                'project_id': projectId,
            },
            query: {
                'chapter_id': chapterId,
                'plan_id': planId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Write Edit Selection
     * Explicitly adopt or switch once, preserving old events on replay.
     * @returns ApiResponse_SelectionRead_ Successful Response
     * @throws ApiError
     */
    public static writeEditSelectionApiV1EditingProjectsProjectIdSelectionsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: SelectionWrite,
    }): CancelablePromise<ApiResponse_SelectionRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/editing/projects/{project_id}/selections',
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
     * Get Edit Selection History
     * Read historical pointers without claiming present selection from an old event.
     * @returns ApiResponse_SelectionHistoryRead_ Successful Response
     * @throws ApiError
     */
    public static getEditSelectionHistoryApiV1EditingProjectsProjectIdSelectionsHistoryGet({
        projectId,
        shotId,
    }: {
        projectId: string,
        shotId: string,
    }): CancelablePromise<ApiResponse_SelectionHistoryRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing/projects/{project_id}/selections/history',
            path: {
                'project_id': projectId,
            },
            query: {
                'shot_id': shotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Edit Drafts
     * Return the unique chapter editing draft, if one exists.
     * @returns ApiResponse_list_EditDraftRead__ Successful Response
     * @throws ApiError
     */
    public static listEditDraftsApiV1EditingProjectsProjectIdDraftsGet({
        projectId,
        chapterId,
    }: {
        projectId: string,
        chapterId: string,
    }): CancelablePromise<ApiResponse_list_EditDraftRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing/projects/{project_id}/drafts',
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
     * Create Edit Draft
     * Save an incomplete controlled assembly without exporting or changing selection.
     * @returns ApiResponse_EditDraftRead_ Successful Response
     * @throws ApiError
     */
    public static createEditDraftApiV1EditingProjectsProjectIdDraftsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: EditDraftCreate,
    }): CancelablePromise<ApiResponse_EditDraftRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/editing/projects/{project_id}/drafts',
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
     * Get Edit Draft
     * Read complete form fields and current content readiness.
     * @returns ApiResponse_EditDraftRead_ Successful Response
     * @throws ApiError
     */
    public static getEditDraftApiV1EditingProjectsProjectIdDraftsDraftIdGet({
        projectId,
        draftId,
    }: {
        projectId: string,
        draftId: string,
    }): CancelablePromise<ApiResponse_EditDraftRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing/projects/{project_id}/drafts/{draft_id}',
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
     * Patch Edit Draft
     * Save a full new CAS revision while retaining immutable history.
     * @returns ApiResponse_EditDraftRead_ Successful Response
     * @throws ApiError
     */
    public static patchEditDraftApiV1EditingProjectsProjectIdDraftsDraftIdPatch({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: EditDraftPatch,
    }): CancelablePromise<ApiResponse_EditDraftRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/editing/projects/{project_id}/drafts/{draft_id}',
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
     * Freeze Edit Draft
     * Lock complete real source facts and human retention declarations.
     * @returns ApiResponse_EditPublicationRead_ Successful Response
     * @throws ApiError
     */
    public static freezeEditDraftApiV1EditingProjectsProjectIdDraftsDraftIdFreezePost({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: EditFreezeWrite,
    }): CancelablePromise<ApiResponse_EditPublicationRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/editing/projects/{project_id}/drafts/{draft_id}/freeze',
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
     * Get Edit Publication
     * Read a portable display view without private filesystem data.
     * @returns ApiResponse_EditPublicationRead_ Successful Response
     * @throws ApiError
     */
    public static getEditPublicationApiV1EditingProjectsProjectIdPublicationsPublicationIdGet({
        projectId,
        publicationId,
    }: {
        projectId: string,
        publicationId: string,
    }): CancelablePromise<ApiResponse_EditPublicationRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing/projects/{project_id}/publications/{publication_id}',
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
     * Preflight Edit Publication
     * Recheck historical source integrity without following later current selections.
     * @returns ApiResponse_EditPreflightRead_ Successful Response
     * @throws ApiError
     */
    public static preflightEditPublicationApiV1EditingProjectsProjectIdPublicationsPublicationIdPreflightGet({
        projectId,
        publicationId,
    }: {
        projectId: string,
        publicationId: string,
    }): CancelablePromise<ApiResponse_EditPreflightRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing/projects/{project_id}/publications/{publication_id}/preflight',
            path: {
                'project_id': projectId,
                'publication_id': publicationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
