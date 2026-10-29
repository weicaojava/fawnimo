/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_AssetImageAvailability_ } from '../models/ApiResponse_AssetImageAvailability_';
import type { ApiResponse_AssetImageJobRead_ } from '../models/ApiResponse_AssetImageJobRead_';
import type { ApiResponse_list_AssetImageSlotRead__ } from '../models/ApiResponse_list_AssetImageSlotRead__';
import type { ApiResponse_PaginatedData_AssetImageJobRead__ } from '../models/ApiResponse_PaginatedData_AssetImageJobRead__';
import type { AssetImageAdopt } from '../models/AssetImageAdopt';
import type { AssetImageRetry } from '../models/AssetImageRetry';
import type { AssetImageSubmit } from '../models/AssetImageSubmit';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AssetImageGenerationService {
    /**
     * Get Asset Image Availability
     * Read only configuration readiness, never testing or invoking any provider.
     * @returns ApiResponse_AssetImageAvailability_ Successful Response
     * @throws ApiError
     */
    public static getAssetImageAvailability({
        modelId,
    }: {
        modelId?: (string | null),
    }): CancelablePromise<ApiResponse_AssetImageAvailability_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/asset-image-generation/availability',
            query: {
                'model_id': modelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Asset Image Slots
     * Read exact current slot snapshots for later confirmed CAS adoption.
     * @returns ApiResponse_list_AssetImageSlotRead__ Successful Response
     * @throws ApiError
     */
    public static listAssetImageSlots({
        kind,
        assetId,
    }: {
        kind: 'actor' | 'costume' | 'scene' | 'prop',
        assetId: string,
    }): CancelablePromise<ApiResponse_list_AssetImageSlotRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/asset-image-generation/assets/{kind}/{asset_id}/slots',
            path: {
                'kind': kind,
                'asset_id': assetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Asset Image Job
     * Admit an explicitly confirmed single text-to-image request without changing an image slot.
     * @returns ApiResponse_AssetImageJobRead_ Successful Response
     * @throws ApiError
     */
    public static createAssetImageJob({
        kind,
        assetId,
        slotId,
        requestBody,
    }: {
        kind: 'actor' | 'costume' | 'scene' | 'prop',
        assetId: string,
        slotId: number,
        requestBody: AssetImageSubmit,
    }): CancelablePromise<ApiResponse_AssetImageJobRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/asset-image-generation/assets/{kind}/{asset_id}/slots/{slot_id}/jobs',
            path: {
                'kind': kind,
                'asset_id': assetId,
                'slot_id': slotId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Asset Image Jobs
     * List persistent candidates and failures belonging to the exact global asset.
     * @returns ApiResponse_PaginatedData_AssetImageJobRead__ Successful Response
     * @throws ApiError
     */
    public static listAssetImageJobs({
        kind,
        assetId,
        page = 1,
        pageSize = 20,
    }: {
        kind: 'actor' | 'costume' | 'scene' | 'prop',
        assetId: string,
        page?: number,
        pageSize?: number,
    }): CancelablePromise<ApiResponse_PaginatedData_AssetImageJobRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/asset-image-generation/assets/{kind}/{asset_id}/jobs',
            path: {
                'kind': kind,
                'asset_id': assetId,
            },
            query: {
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Asset Image Job
     * Load actual persistent job state after a refresh or runtime restart.
     * @returns ApiResponse_AssetImageJobRead_ Successful Response
     * @throws ApiError
     */
    public static getAssetImageJob({
        jobId,
    }: {
        jobId: string,
    }): CancelablePromise<ApiResponse_AssetImageJobRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/asset-image-generation/jobs/{job_id}',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cancel Asset Image Job
     * Cancel queued/in-flight work; immutable completed candidates remain available.
     * @returns ApiResponse_AssetImageJobRead_ Successful Response
     * @throws ApiError
     */
    public static cancelAssetImageJob({
        jobId,
    }: {
        jobId: string,
    }): CancelablePromise<ApiResponse_AssetImageJobRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/asset-image-generation/jobs/{job_id}/cancel',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Retry Asset Image Job
     * Create a separate explicit attempt; never silently retry a possibly charged image call.
     * @returns ApiResponse_AssetImageJobRead_ Successful Response
     * @throws ApiError
     */
    public static retryAssetImageJob({
        jobId,
        requestBody,
    }: {
        jobId: string,
        requestBody: AssetImageRetry,
    }): CancelablePromise<ApiResponse_AssetImageJobRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/asset-image-generation/jobs/{job_id}/retry',
            path: {
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
     * Get Asset Image Candidate Content
     * Serve only local, hash-verified candidate bytes; never redirect to an upstream URL.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getAssetImageCandidateContent({
        jobId,
    }: {
        jobId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/asset-image-generation/jobs/{job_id}/content',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Adopt Asset Image Candidate
     * Change one asset-owned slot only after explicit confirmation and current snapshot CAS.
     * @returns ApiResponse_AssetImageJobRead_ Successful Response
     * @throws ApiError
     */
    public static adoptAssetImageCandidate({
        jobId,
        requestBody,
    }: {
        jobId: string,
        requestBody: AssetImageAdopt,
    }): CancelablePromise<ApiResponse_AssetImageJobRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/asset-image-generation/jobs/{job_id}/adopt',
            path: {
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
