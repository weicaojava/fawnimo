/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_dict_ } from '../models/ApiResponse_dict_';
import type { ApiResponse_list_dict__ } from '../models/ApiResponse_list_dict__';
import type { ArchiveWrite } from '../models/ArchiveWrite';
import type { AssetWrite } from '../models/AssetWrite';
import type { Body_post_bundle_api_v1_studio_production_projects__project_id__media_bundle_post } from '../models/Body_post_bundle_api_v1_studio_production_projects__project_id__media_bundle_post';
import type { Body_post_media_api_v1_studio_production_projects__project_id__media_post } from '../models/Body_post_media_api_v1_studio_production_projects__project_id__media_post';
import type { TakeWrite } from '../models/TakeWrite';
import type { VersionWrite } from '../models/VersionWrite';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ProductionService {
    /**
     * List Assets
     * Read this project's versioned library; global legacy browsing is separate.
     * @returns ApiResponse_list_dict__ Successful Response
     * @throws ApiError
     */
    public static listAssetsApiV1StudioProductionProjectsProjectIdAssetsGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_list_dict__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/production/projects/{project_id}/assets',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Asset
     * Create a real project asset and initial snapshot.
     * @returns ApiResponse_dict_ Successful Response
     * @throws ApiError
     */
    public static postAssetApiV1StudioProductionProjectsProjectIdAssetsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: AssetWrite,
    }): CancelablePromise<ApiResponse_dict_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production/projects/{project_id}/assets',
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
     * Get Asset
     * Inspect previous revisions and their affected shots.
     * @returns ApiResponse_dict_ Successful Response
     * @throws ApiError
     */
    public static getAssetApiV1StudioProductionProjectsProjectIdAssetsAssetIdGet({
        projectId,
        assetId,
    }: {
        projectId: string,
        assetId: string,
    }): CancelablePromise<ApiResponse_dict_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/production/projects/{project_id}/assets/{asset_id}',
            path: {
                'project_id': projectId,
                'asset_id': assetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Version
     * Append a revision without mutating history.
     * @returns ApiResponse_dict_ Successful Response
     * @throws ApiError
     */
    public static postVersionApiV1StudioProductionProjectsProjectIdAssetsAssetIdVersionsPost({
        projectId,
        assetId,
        requestBody,
    }: {
        projectId: string,
        assetId: string,
        requestBody: VersionWrite,
    }): CancelablePromise<ApiResponse_dict_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production/projects/{project_id}/assets/{asset_id}/versions',
            path: {
                'project_id': projectId,
                'asset_id': assetId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Archive Asset
     * Archive/recover the asset identity while preserving references.
     * @returns ApiResponse_dict_ Successful Response
     * @throws ApiError
     */
    public static archiveAssetApiV1StudioProductionProjectsProjectIdAssetsAssetIdArchivePatch({
        projectId,
        assetId,
        requestBody,
    }: {
        projectId: string,
        assetId: string,
        requestBody: ArchiveWrite,
    }): CancelablePromise<ApiResponse_dict_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/production/projects/{project_id}/assets/{asset_id}/archive',
            path: {
                'project_id': projectId,
                'asset_id': assetId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Media
     * List only media owned by the selected project.
     * @returns ApiResponse_list_dict__ Successful Response
     * @throws ApiError
     */
    public static listMediaApiV1StudioProductionProjectsProjectIdMediaGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_list_dict__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/production/projects/{project_id}/media',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Media
     * Import bounded binary media; the supplied name is never a storage path.
     * @returns ApiResponse_list_dict__ Successful Response
     * @throws ApiError
     */
    public static postMediaApiV1StudioProductionProjectsProjectIdMediaPost({
        projectId,
        formData,
    }: {
        projectId: string,
        formData: Body_post_media_api_v1_studio_production_projects__project_id__media_post,
    }): CancelablePromise<ApiResponse_list_dict__> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production/projects/{project_id}/media',
            path: {
                'project_id': projectId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Bundle
     * Validate a complete media-only bundle before writing any member.
     * @returns ApiResponse_list_dict__ Successful Response
     * @throws ApiError
     */
    public static postBundleApiV1StudioProductionProjectsProjectIdMediaBundlePost({
        projectId,
        formData,
    }: {
        projectId: string,
        formData: Body_post_bundle_api_v1_studio_production_projects__project_id__media_bundle_post,
    }): CancelablePromise<ApiResponse_list_dict__> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production/projects/{project_id}/media-bundle',
            path: {
                'project_id': projectId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Content
     * Serve hash-verified local content with explicit ownership checks.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getContentApiV1StudioProductionProjectsProjectIdMediaMediaIdContentGet({
        projectId,
        mediaId,
    }: {
        projectId: string,
        mediaId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/production/projects/{project_id}/media/{media_id}/content',
            path: {
                'project_id': projectId,
                'media_id': mediaId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Take
     * Record imported adoption without inventing model provenance.
     * @returns ApiResponse_dict_ Successful Response
     * @throws ApiError
     */
    public static postTakeApiV1StudioProductionProjectsProjectIdTakesPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: TakeWrite,
    }): CancelablePromise<ApiResponse_dict_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production/projects/{project_id}/takes',
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
     * List Takes
     * Inspect immutable take history in the selected project.
     * @returns ApiResponse_list_dict__ Successful Response
     * @throws ApiError
     */
    public static listTakesApiV1StudioProductionProjectsProjectIdTakesGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_list_dict__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/production/projects/{project_id}/takes',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Take
     * Read the exact adopted versions.
     * @returns ApiResponse_dict_ Successful Response
     * @throws ApiError
     */
    public static getTakeApiV1StudioProductionProjectsProjectIdTakesTakeIdGet({
        projectId,
        takeId,
    }: {
        projectId: string,
        takeId: string,
    }): CancelablePromise<ApiResponse_dict_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/production/projects/{project_id}/takes/{take_id}',
            path: {
                'project_id': projectId,
                'take_id': takeId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Selection
     * Select a previous take without rewriting its snapshots.
     * @returns ApiResponse_dict_ Successful Response
     * @throws ApiError
     */
    public static postSelectionApiV1StudioProductionProjectsProjectIdTakesTakeIdSelectPost({
        projectId,
        takeId,
    }: {
        projectId: string,
        takeId: string,
    }): CancelablePromise<ApiResponse_dict_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production/projects/{project_id}/takes/{take_id}/select',
            path: {
                'project_id': projectId,
                'take_id': takeId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
