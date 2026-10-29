/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_ProjectBudgetRead_ } from '../models/ApiResponse_ProjectBudgetRead_';
import type { ApiResponse_VideoExecutionPlanPreview_ } from '../models/ApiResponse_VideoExecutionPlanPreview_';
import type { ApiResponse_VideoRuntimeSetupRead_ } from '../models/ApiResponse_VideoRuntimeSetupRead_';
import type { ProjectBudgetWrite } from '../models/ProjectBudgetWrite';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class VideoRuntimeService {
    /**
     * Get Video Runtime Setup
     * Read availability without touching credentials, accepting jobs or returning private paths.
     * @returns ApiResponse_VideoRuntimeSetupRead_ Successful Response
     * @throws ApiError
     */
    public static getVideoRuntimeSetup(): CancelablePromise<ApiResponse_VideoRuntimeSetupRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-runtime/setup',
        });
    }
    /**
     * Get Video Configuration Template
     * Download an inert server-owned template; no key, endpoint override or guessed price is included.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getVideoConfigurationTemplate(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-runtime/configuration-template',
        });
    }
    /**
     * Get Video Project Budget
     * Show this project's actual ledger without reading or altering another project.
     * @returns ApiResponse_ProjectBudgetRead_ Successful Response
     * @throws ApiError
     */
    public static getVideoProjectBudget({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_ProjectBudgetRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-runtime/projects/{project_id}/budget',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Save Video Project Budget
     * Save an explicitly chosen ceiling; saving does not start generation or settle provider charges.
     * @returns ApiResponse_ProjectBudgetRead_ Successful Response
     * @throws ApiError
     */
    public static saveVideoProjectBudget({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: ProjectBudgetWrite,
    }): CancelablePromise<ApiResponse_ProjectBudgetRead_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/v1/video-runtime/projects/{project_id}/budget',
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
     * Preview Executable Video Plan
     * Read the opted-in transport body; no task, reservation or remote request is created.
     * @returns ApiResponse_VideoExecutionPlanPreview_ Successful Response
     * @throws ApiError
     */
    public static previewExecutableVideoPlan({
        projectId,
        planId,
    }: {
        projectId: string,
        planId: string,
    }): CancelablePromise<ApiResponse_VideoExecutionPlanPreview_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-runtime/projects/{project_id}/plans/{plan_id}/request-preview',
            path: {
                'project_id': projectId,
                'plan_id': planId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
