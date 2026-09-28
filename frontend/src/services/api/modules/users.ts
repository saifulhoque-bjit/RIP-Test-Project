import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  AssignUserProjectsResponse,
  ProjectAssignmentInput,
  UpdateUserRoleResponse,
  UserListResponse,
} from "@/types";

const usersApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getUsers: build.query<UserListResponse, { tenant_id?: string } | void>({
      query: (params) => ({
        url: API_ENDPOINTS.USERS.GET_LIST,
        params: {
          ...(params?.tenant_id ? { tenant_id: params.tenant_id } : {}),
        },
      }),
      providesTags: (result) =>
        result?.data
          ? [
              ...result.data.map(({ id }) => ({ type: "User" as const, id })),
              { type: "User", id: "LIST" },
            ]
          : [{ type: "User", id: "LIST" }],
    }),

    updateUserRole: build.mutation<
      UpdateUserRoleResponse,
      { userId: string; roleName: string }
    >({
      query: ({ userId, roleName }) => ({
        url: API_ENDPOINTS.USERS.UPDATE_ROLE(userId),
        method: "POST",
        body: { role_name: roleName },
      }),
      invalidatesTags: (_result, _error, { userId }) => [
        { type: "User", id: userId },
        { type: "User", id: "LIST" },
      ],
    }),

    assignUserProjects: build.mutation<
      AssignUserProjectsResponse,
      { userId: string; assignments: ProjectAssignmentInput[] }
    >({
      query: ({ userId, assignments }) => ({
        url: API_ENDPOINTS.USERS.ASSIGN_PROJECTS(userId),
        method: "POST",
        body: { assignments },
      }),
      invalidatesTags: (_result, _error, { userId }) => [
        { type: "User", id: userId },
        { type: "User", id: "LIST" },
      ],
    }),

    updateUserStatus: build.mutation<
      { success: boolean; message: string },
      { userId: string; isActive: boolean }
    >({
      query: ({ userId, isActive }) => ({
        url: API_ENDPOINTS.USERS.UPDATE_STATUS(userId),
        method: "PATCH",
        body: { is_active: isActive },
      }),
      invalidatesTags: (_result, _error, { userId }) => [
        { type: "User", id: userId },
        { type: "User", id: "LIST" },
      ],
    }),

    deleteUser: build.mutation<
      { success: boolean; message: string },
      { userId: string }
    >({
      query: ({ userId }) => ({
        url: API_ENDPOINTS.USERS.DELETE(userId),
        method: "DELETE",
      }),
      invalidatesTags: (_result, _error, { userId }) => [
        { type: "User", id: userId },
        { type: "User", id: "LIST" },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetUsersQuery,
  useUpdateUserRoleMutation,
  useAssignUserProjectsMutation,
  useUpdateUserStatusMutation,
  useDeleteUserMutation,
} = usersApi;
export default usersApi;
