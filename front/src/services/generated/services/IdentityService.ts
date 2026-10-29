/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_DemoRead_ } from '../models/ApiResponse_DemoRead_';
import type { ApiResponse_dict_ } from '../models/ApiResponse_dict_';
import type { ApiResponse_IdentityRead_ } from '../models/ApiResponse_IdentityRead_';
import type { ApiResponse_IdentityStatus_ } from '../models/ApiResponse_IdentityStatus_';
import type { DemoCreate } from '../models/DemoCreate';
import type { LoginRequest } from '../models/LoginRequest';
import type { ProfileRequest } from '../models/ProfileRequest';
import type { RegisterRequest } from '../models/RegisterRequest';
import type { WorkspaceSwitch } from '../models/WorkspaceSwitch';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class IdentityService {
    /**
     * Identity Status
     * Let the same built studio choose local entry or the real login flow.
     * @returns ApiResponse_IdentityStatus_ Successful Response
     * @throws ApiError
     */
    public static identityStatusApiV1IdentityStatusGet(): CancelablePromise<ApiResponse_IdentityStatus_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/identity/status',
        });
    }
    /**
     * Login
     * Authenticate one bounded attempt and start a new session without exposing its token.
     * @returns ApiResponse_IdentityRead_ Successful Response
     * @throws ApiError
     */
    public static loginApiV1IdentityLoginPost({
        requestBody,
    }: {
        requestBody: LoginRequest,
    }): CancelablePromise<ApiResponse_IdentityRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/identity/login',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Register
     * Provision only when the operator explicitly enables public account registration.
     * @returns ApiResponse_IdentityRead_ Successful Response
     * @throws ApiError
     */
    public static registerApiV1IdentityRegisterPost({
        requestBody,
    }: {
        requestBody: RegisterRequest,
    }): CancelablePromise<ApiResponse_IdentityRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/identity/register',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Me
     * Recheck membership and return only the current user's safe identity fields.
     * @returns ApiResponse_IdentityRead_ Successful Response
     * @throws ApiError
     */
    public static meApiV1IdentityMeGet(): CancelablePromise<ApiResponse_IdentityRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/identity/me',
        });
    }
    /**
     * Profile
     * Update personal preferences without accepting role, ownership or identity changes.
     * @returns ApiResponse_IdentityRead_ Successful Response
     * @throws ApiError
     */
    public static profileApiV1IdentityMePatch({
        requestBody,
    }: {
        requestBody: ProfileRequest,
    }): CancelablePromise<ApiResponse_IdentityRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/identity/me',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Switch
     * Switch only to a current membership; the client reloads before accessing new data.
     * @returns ApiResponse_IdentityRead_ Successful Response
     * @throws ApiError
     */
    public static switchApiV1IdentitySwitchPost({
        requestBody,
    }: {
        requestBody: WorkspaceSwitch,
    }): CancelablePromise<ApiResponse_IdentityRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/identity/switch',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Logout
     * Persistently revoke the session and remove the browser's cookie.
     * @returns ApiResponse_dict_ Successful Response
     * @throws ApiError
     */
    public static logoutApiV1IdentityLogoutPost(): CancelablePromise<ApiResponse_dict_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/identity/logout',
        });
    }
    /**
     * Create Example
     * Create a fresh editable example, preserving all previous examples and user edits.
     * @returns ApiResponse_DemoRead_ Successful Response
     * @throws ApiError
     */
    public static createExampleApiV1IdentityDemoPost({
        requestBody,
    }: {
        requestBody: DemoCreate,
    }): CancelablePromise<ApiResponse_DemoRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/identity/demo',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
