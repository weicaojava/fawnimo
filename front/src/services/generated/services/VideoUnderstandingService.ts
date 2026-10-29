/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_list_UnderstandingJobRead__ } from '../models/ApiResponse_list_UnderstandingJobRead__';
import type { ApiResponse_UnderstandingConnectionRead_ } from '../models/ApiResponse_UnderstandingConnectionRead_';
import type { ApiResponse_UnderstandingDraftRead_ } from '../models/ApiResponse_UnderstandingDraftRead_';
import type { ApiResponse_UnderstandingJobRead_ } from '../models/ApiResponse_UnderstandingJobRead_';
import type { UnderstandingAttemptControl } from '../models/UnderstandingAttemptControl';
import type { UnderstandingConnectionWrite } from '../models/UnderstandingConnectionWrite';
import type { UnderstandingCreate } from '../models/UnderstandingCreate';
import type { UnderstandingDraftCreate } from '../models/UnderstandingDraftCreate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class VideoUnderstandingService {
    /**
     * Get Connection
     * Explain model setup and dedicated authorization state without making network requests.
     * @returns ApiResponse_UnderstandingConnectionRead_ Successful Response
     * @throws ApiError
     */
    public static getVideoUnderstandingConnection({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_UnderstandingConnectionRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-understanding/projects/{project_id}/connection',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Put Connection
     * Save only this project's explicit model connection; credentials are never returned or imported.
     * @returns ApiResponse_UnderstandingConnectionRead_ Successful Response
     * @throws ApiError
     */
    public static saveVideoUnderstandingConnection({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: UnderstandingConnectionWrite,
    }): CancelablePromise<ApiResponse_UnderstandingConnectionRead_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/v1/video-understanding/projects/{project_id}/connection',
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
     * Get Jobs
     * Read actual persisted attempts and candidates without simulated success records.
     * @returns ApiResponse_list_UnderstandingJobRead__ Successful Response
     * @throws ApiError
     */
    public static listVideoUnderstandingJobs({
        projectId,
        limit = 30,
    }: {
        projectId: string,
        limit?: number,
    }): CancelablePromise<ApiResponse_list_UnderstandingJobRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-understanding/projects/{project_id}/jobs',
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
     * Post Job
     * Store source/config intent before the worker samples or sends any media.
     * @returns ApiResponse_UnderstandingJobRead_ Successful Response
     * @throws ApiError
     */
    public static createVideoUnderstandingJob({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: UnderstandingCreate,
    }): CancelablePromise<ApiResponse_UnderstandingJobRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/video-understanding/projects/{project_id}/jobs',
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
     * Get Job
     * Reopen task progress, real evidence receipts, unreviewed transcripts and the final candidate.
     * @returns ApiResponse_UnderstandingJobRead_ Successful Response
     * @throws ApiError
     */
    public static getVideoUnderstandingJob({
        projectId,
        jobId,
    }: {
        projectId: string,
        jobId: string,
    }): CancelablePromise<ApiResponse_UnderstandingJobRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-understanding/projects/{project_id}/jobs/{job_id}',
            path: {
                'project_id': projectId,
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Cancel
     * Fence publication first, then let the worker close its in-flight HTTP request.
     * @returns ApiResponse_UnderstandingJobRead_ Successful Response
     * @throws ApiError
     */
    public static cancelVideoUnderstandingJob({
        projectId,
        jobId,
        requestBody,
    }: {
        projectId: string,
        jobId: string,
        requestBody: UnderstandingAttemptControl,
    }): CancelablePromise<ApiResponse_UnderstandingJobRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/video-understanding/projects/{project_id}/jobs/{job_id}/cancel',
            path: {
                'project_id': projectId,
                'job_id': jobId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Retry
     * A human explicitly retries the original saved connection; old attempts remain visible.
     * @returns ApiResponse_UnderstandingJobRead_ Successful Response
     * @throws ApiError
     */
    public static retryVideoUnderstandingJob({
        projectId,
        jobId,
        requestBody,
    }: {
        projectId: string,
        jobId: string,
        requestBody: UnderstandingAttemptControl,
    }): CancelablePromise<ApiResponse_UnderstandingJobRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/video-understanding/projects/{project_id}/jobs/{job_id}/retry',
            path: {
                'project_id': projectId,
                'job_id': jobId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Draft
     * Create one editable, unreviewed DNA draft only after original human-confirmed cuts match.
     * @returns ApiResponse_UnderstandingDraftRead_ Successful Response
     * @throws ApiError
     */
    public static createVideoUnderstandingDraft({
        projectId,
        jobId,
        requestBody,
    }: {
        projectId: string,
        jobId: string,
        requestBody: UnderstandingDraftCreate,
    }): CancelablePromise<ApiResponse_UnderstandingDraftRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/video-understanding/projects/{project_id}/jobs/{job_id}/draft',
            path: {
                'project_id': projectId,
                'job_id': jobId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
