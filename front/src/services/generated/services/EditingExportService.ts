/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_ExportCatalog_ } from '../models/ApiResponse_ExportCatalog_';
import type { ApiResponse_ExportRead_ } from '../models/ApiResponse_ExportRead_';
import type { ExportCancel } from '../models/ExportCancel';
import type { ExportRequest } from '../models/ExportRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class EditingExportService {
    /**
     * List Editing Exports
     * Return actual bounded local job history and tool availability.
     * @returns ApiResponse_ExportCatalog_ Successful Response
     * @throws ApiError
     */
    public static listEditingExports({
        projectId,
        chapterId,
    }: {
        projectId: string,
        chapterId: string,
    }): CancelablePromise<ApiResponse_ExportCatalog_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing/projects/{project_id}/exports',
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
     * Create Editing Export
     * Persist one explicit local render of the selected immutable timeline.
     * @returns ApiResponse_ExportRead_ Successful Response
     * @throws ApiError
     */
    public static createEditingExport({
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
            url: '/api/v1/editing/projects/{project_id}/publications/{publication_id}/exports',
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
     * Get Editing Export
     * Read durable status and verified ready files after a page or application restart.
     * @returns ApiResponse_ExportRead_ Successful Response
     * @throws ApiError
     */
    public static getEditingExport({
        projectId,
        exportId,
    }: {
        projectId: string,
        exportId: string,
    }): CancelablePromise<ApiResponse_ExportRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/editing/projects/{project_id}/exports/{export_id}',
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
     * Cancel Editing Export
     * Request cancellation without accepting client-provided worker state.
     * @returns ApiResponse_ExportRead_ Successful Response
     * @throws ApiError
     */
    public static cancelEditingExport({
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
            url: '/api/v1/editing/projects/{project_id}/exports/{export_id}/cancel',
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
     * Read Editing Export Content
     * Stream a verified owned artifact with safe fixed download filenames.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static readEditingExportContent({
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
            url: '/api/v1/editing/projects/{project_id}/exports/{export_id}/content/{kind}',
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
