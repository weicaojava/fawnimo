/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_ExecutionRead_ } from '../models/ApiResponse_ExecutionRead_';
import type { ApiResponse_list_ExecutionRead__ } from '../models/ApiResponse_list_ExecutionRead__';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ExecutionService {
    /**
     * Get Execution Jobs
     * 查看本工程任务；不公开远程结果地址、请求快照或凭据。
     * @returns ApiResponse_list_ExecutionRead__ Successful Response
     * @throws ApiError
     */
    public static getExecutionJobsApiV1ExecutionProjectsProjectIdJobsGet({
        projectId,
        offset,
        limit = 50,
    }: {
        projectId: string,
        offset?: number,
        limit?: number,
    }): CancelablePromise<ApiResponse_list_ExecutionRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/execution/projects/{project_id}/jobs',
            path: {
                'project_id': projectId,
            },
            query: {
                'offset': offset,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Execution Job
     * 查看工程内单个任务的安全状态。
     * @returns ApiResponse_ExecutionRead_ Successful Response
     * @throws ApiError
     */
    public static getExecutionJobApiV1ExecutionProjectsProjectIdJobsJobIdGet({
        projectId,
        jobId,
    }: {
        projectId: string,
        jobId: string,
    }): CancelablePromise<ApiResponse_ExecutionRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/execution/projects/{project_id}/jobs/{job_id}',
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
     * Pause Execution Job
     * 暂停后续查询或下载；正在执行的一步需先结束。
     * @returns ApiResponse_ExecutionRead_ Successful Response
     * @throws ApiError
     */
    public static pauseExecutionJobApiV1ExecutionProjectsProjectIdJobsJobIdPausePost({
        projectId,
        jobId,
    }: {
        projectId: string,
        jobId: string,
    }): CancelablePromise<ApiResponse_ExecutionRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/execution/projects/{project_id}/jobs/{job_id}/pause',
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
     * Resume Execution Job
     * 仅恢复已暂停任务的查询或下载，不创建或重新提交请求。
     * @returns ApiResponse_ExecutionRead_ Successful Response
     * @throws ApiError
     */
    public static resumeExecutionJobApiV1ExecutionProjectsProjectIdJobsJobIdResumePost({
        projectId,
        jobId,
    }: {
        projectId: string,
        jobId: string,
    }): CancelablePromise<ApiResponse_ExecutionRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/execution/projects/{project_id}/jobs/{job_id}/resume',
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
     * Cancel Execution Job
     * 记录取消意图；已提交任务需等待远端确认，提交前可本地取消。
     * @returns ApiResponse_ExecutionRead_ Successful Response
     * @throws ApiError
     */
    public static cancelExecutionJobApiV1ExecutionProjectsProjectIdJobsJobIdCancelPost({
        projectId,
        jobId,
    }: {
        projectId: string,
        jobId: string,
    }): CancelablePromise<ApiResponse_ExecutionRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/execution/projects/{project_id}/jobs/{job_id}/cancel',
            path: {
                'project_id': projectId,
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
