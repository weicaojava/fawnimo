/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Require a separately confirmed, exact slot snapshot before changing its image.
 */
export type AssetImageAdopt = {
    request_key: string;
    expected_file_id: (string | null);
    expected_updated_at: string;
    confirm_replace: boolean;
};

