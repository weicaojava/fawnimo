/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AnalysisCreate } from '../models/AnalysisCreate';
import type { ApiResponse_AnalysisRead_ } from '../models/ApiResponse_AnalysisRead_';
import type { ApiResponse_ImportedMedia_ } from '../models/ApiResponse_ImportedMedia_';
import type { ApiResponse_list_AnalysisRead__ } from '../models/ApiResponse_list_AnalysisRead__';
import type { CutsWrite } from '../models/CutsWrite';
import type { UrlImport } from '../models/UrlImport';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ReverseService {
    /**
     * Post Reverse Url
     * Import a trusted public URL; local upload remains available without config.
     * @returns ApiResponse_ImportedMedia_ Successful Response
     * @throws ApiError
     */
    public static postReverseUrlApiV1ReverseProjectsProjectIdImportUrlPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: UrlImport,
    }): CancelablePromise<ApiResponse_ImportedMedia_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/reverse/projects/{project_id}/import-url',
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
     * Get Reverse Analyses
     * List lightweight technical analyses belonging to this project.
     * @returns ApiResponse_list_AnalysisRead__ Successful Response
     * @throws ApiError
     */
    public static getReverseAnalysesApiV1ReverseProjectsProjectIdAnalysesGet({
        projectId,
        limit = 50,
    }: {
        projectId: string,
        limit?: number,
    }): CancelablePromise<ApiResponse_list_AnalysisRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/reverse/projects/{project_id}/analyses',
            path: {
                'project_id': projectId,
            },
            query: {
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Reverse Analysis
     * Persist work before processing; the lifecycle scanner recovers it after restart.
     * @returns ApiResponse_AnalysisRead_ Successful Response
     * @throws ApiError
     */
    public static postReverseAnalysisApiV1ReverseProjectsProjectIdAnalysesPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: AnalysisCreate,
    }): CancelablePromise<ApiResponse_AnalysisRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/reverse/projects/{project_id}/analyses',
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
     * Get Reverse Analysis
     * Read original frame clocks, proxy mapping and candidate evidence.
     * @returns ApiResponse_AnalysisRead_ Successful Response
     * @throws ApiError
     */
    public static getReverseAnalysisApiV1ReverseProjectsProjectIdAnalysesAnalysisIdGet({
        projectId,
        analysisId,
    }: {
        projectId: string,
        analysisId: string,
    }): CancelablePromise<ApiResponse_AnalysisRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/reverse/projects/{project_id}/analyses/{analysis_id}',
            path: {
                'project_id': projectId,
                'analysis_id': analysisId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Patch Reverse Cuts
     * Confirm manually reviewed boundaries with version conflict protection.
     * @returns ApiResponse_AnalysisRead_ Successful Response
     * @throws ApiError
     */
    public static patchReverseCutsApiV1ReverseProjectsProjectIdAnalysesAnalysisIdCutsPatch({
        projectId,
        analysisId,
        requestBody,
    }: {
        projectId: string,
        analysisId: string,
        requestBody: CutsWrite,
    }): CancelablePromise<ApiResponse_AnalysisRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/reverse/projects/{project_id}/analyses/{analysis_id}/cuts',
            path: {
                'project_id': projectId,
                'analysis_id': analysisId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Reverse Proxy
     * Serve only the owned normalized preview video.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getReverseProxyApiV1ReverseProjectsProjectIdAnalysesAnalysisIdProxyGet({
        projectId,
        analysisId,
    }: {
        projectId: string,
        analysisId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/reverse/projects/{project_id}/analyses/{analysis_id}/proxy',
            path: {
                'project_id': projectId,
                'analysis_id': analysisId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Reverse Audio
     * Serve an extracted audio track without claiming any transcription.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getReverseAudioApiV1ReverseProjectsProjectIdAnalysesAnalysisIdAudioGet({
        projectId,
        analysisId,
    }: {
        projectId: string,
        analysisId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/reverse/projects/{project_id}/analyses/{analysis_id}/audio',
            path: {
                'project_id': projectId,
                'analysis_id': analysisId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Reverse Frame
     * Serve exact original frames for candidate and manually added boundaries.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getReverseFrameApiV1ReverseProjectsProjectIdAnalysesAnalysisIdFramesFrameIndexGet({
        projectId,
        analysisId,
        frameIndex,
    }: {
        projectId: string,
        analysisId: string,
        frameIndex: number,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/reverse/projects/{project_id}/analyses/{analysis_id}/frames/{frame_index}',
            path: {
                'project_id': projectId,
                'analysis_id': analysisId,
                'frame_index': frameIndex,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
