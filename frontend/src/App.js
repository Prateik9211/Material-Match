import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider } from "@/context/AuthContext";
import ProtectedRoute from "@/components/ProtectedRoute";
import Landing from "@/pages/Landing";
import Auth from "@/pages/Auth";
import Dashboard from "@/pages/Dashboard";
import NewProject from "@/pages/NewProject";
import Analysis from "@/pages/Analysis";
import Match from "@/pages/Match";
import AdminAffiliates from "@/pages/AdminAffiliates";
import AdminKnowledgeEngine from "@/pages/AdminKnowledgeEngine";
import AdminReviews from "@/pages/AdminReviews";
import AdminUsers from "@/pages/AdminUsers";
import LeaveReview from "@/pages/LeaveReview";
import MaterialMatchStudio from "@/pages/MaterialMatchStudio";
import ConceptWorkspace from "@/pages/ConceptWorkspace";
import PublicRoom from "@/pages/PublicRoom";
import Demo from "@/pages/Demo";
import MaterialLibrary from "@/pages/MaterialLibrary";
import MaterialLibraryCategory from "@/pages/MaterialLibraryCategory";
import AdminSceneTest from "@/pages/AdminSceneTest";

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Toaster position="top-right" theme="light" />
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/auth" element={<Auth />} />
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <Dashboard />
              </ProtectedRoute>
            }
          />
          <Route
            path="/projects/new"
            element={
              <ProtectedRoute>
                <NewProject />
              </ProtectedRoute>
            }
          />
          <Route
            path="/projects/:id/analysis"
            element={
              <ProtectedRoute>
                <Analysis />
              </ProtectedRoute>
            }
          />
          <Route
            path="/projects/:id/match"
            element={
              <ProtectedRoute>
                <Match />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/affiliates"
            element={
              <ProtectedRoute>
                <AdminAffiliates />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/reviews"
            element={
              <ProtectedRoute>
                <AdminReviews />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/users"
            element={
              <ProtectedRoute>
                <AdminUsers />
              </ProtectedRoute>
            }
          />
          <Route
            path="/reviews/new"
            element={
              <ProtectedRoute>
                <LeaveReview />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/knowledge-engine"
            element={
              <ProtectedRoute>
                <AdminKnowledgeEngine />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/studio"
            element={
              <ProtectedRoute>
                <MaterialMatchStudio />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/scene-test"
            element={
              <ProtectedRoute>
                <AdminSceneTest />
              </ProtectedRoute>
            }
          />
          <Route
            path="/projects/:id/concept"
            element={
              <ProtectedRoute>
                <ConceptWorkspace />
              </ProtectedRoute>
            }
          />
          <Route path="/share/rooms/:slug" element={<PublicRoom />} />
          <Route path="/demo" element={<Demo />} />
          <Route
            path="/library"
            element={
              <ProtectedRoute>
                <MaterialLibrary />
              </ProtectedRoute>
            }
          />
          <Route
            path="/library/:category"
            element={
              <ProtectedRoute>
                <MaterialLibraryCategory />
              </ProtectedRoute>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
